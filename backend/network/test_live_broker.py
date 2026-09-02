"""The on-demand live reader.

What matters here is the lifecycle, not the arithmetic: the connection must
open when somebody looks, close when they stop, and never be opened twice for
one router however many workers or viewers there are.
"""
import threading
import time
from decimal import Decimal
from unittest import mock

from django.test import TransactionTestCase
from django.utils import timezone

from billing.models import Service, Tariff
from customers.models import Customer
from network import live_broker, mikrotik
from network.models import Device
from radiusauth.models import LiveTrafficInterest, RouterLiveRate
from radiusauth.usage import request_live_readings


class FakeApi:
    """Counts how many times the caller reads, so a held connection can be
    told apart from a reconnect-per-read."""

    def __init__(self):
        self.reads = 0
        self.rx = 1000
        self.tx = 5000


class LiveBrokerTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.device = Device.objects.create(
            name="Bench", ip_address="10.0.0.1", api_enabled=True,
            api_username="u", api_password="p",
        )
        self.connections = 0
        live_broker._threads.clear()

    def tearDown(self):
        # A reader from the previous test still holds the Postgres advisory
        # lock, so the next test's reader correctly declines to start and the
        # test fails for a reason that has nothing to do with what it asserts.
        # Expire every interest row and let the threads drain.
        LiveTrafficInterest.objects.update(
            last_requested_at=timezone.now() - timezone.timedelta(hours=1)
        )
        self._wait(lambda: not live_broker._threads, timeout=10)
        live_broker._threads.clear()

    def _patched(self, api=None, on_read=None):
        api = api or FakeApi()
        broker = self

        class _Conn:
            def __enter__(self_inner):
                broker.connections += 1
                return api
            def __exit__(self_inner, *a):
                return False

        def read(passed_api):
            passed_api.reads += 1
            if on_read:
                on_read(passed_api)
            passed_api.rx += 12500      # 100 kbit at 1s
            passed_api.tx += 125000     # 1 Mbit at 1s
            return {"pppoe_test": {"rx_byte": passed_api.rx, "tx_byte": passed_api.tx,
                                   "interface": "<pppoe-pppoe_test>"}}

        return (
            mock.patch.object(mikrotik, "api_connection", lambda d, timeout=8: _Conn()),
            mock.patch.object(mikrotik, "read_session_traffic", read),
            api,
        )

    def _fresh(self):
        LiveTrafficInterest.objects.update_or_create(
            device_id=self.device.pk, defaults={"last_requested_at": timezone.now()}
        )

    def _wait(self, predicate, timeout=8):
        end = time.time() + timeout
        while time.time() < end:
            if predicate():
                return True
            time.sleep(0.05)
        return False

    # ---- the headline behaviour ----------------------------------------

    def test_one_connection_serves_many_reads(self):
        """The point of the rework: hold the connection, don't relogin."""
        conn_patch, read_patch, api = self._patched()
        with conn_patch, read_patch, mock.patch.object(live_broker, "POLL_SECONDS", 0.05):
            self._fresh()
            live_broker._ensure_reader(self.device.pk)
            self.assertTrue(self._wait(lambda: api.reads >= 5), "reader never got going")
        self.assertEqual(self.connections, 1, f"{self.connections} logins for {api.reads} reads")

    def test_it_stops_when_nobody_is_watching(self):
        conn_patch, read_patch, api = self._patched()
        with conn_patch, read_patch, mock.patch.object(live_broker, "POLL_SECONDS", 0.05):
            self._fresh()
            live_broker._ensure_reader(self.device.pk)
            self.assertTrue(self._wait(lambda: api.reads >= 2), "reader never got going")

            # Expire the interest outright rather than waiting on a short TTL:
            # the thread can take a moment to start under a test database, and
            # racing it proves nothing about the behaviour being asserted.
            LiveTrafficInterest.objects.filter(device=self.device).update(
                last_requested_at=timezone.now() - timezone.timedelta(hours=1)
            )
            self.assertTrue(
                self._wait(lambda: not live_broker._threads, timeout=10),
                "the reader kept the router connection open with nobody watching",
            )
        reads_at_stop = api.reads
        time.sleep(0.3)
        self.assertEqual(api.reads, reads_at_stop, "it carried on reading after stopping")

    def test_a_second_worker_does_not_open_a_second_connection(self):
        """Gunicorn runs several workers; without the advisory lock each one
        would start its own reader and its own router connection."""
        conn_patch, read_patch, api = self._patched()
        with conn_patch, read_patch, mock.patch.object(live_broker, "POLL_SECONDS", 0.05):
            self._fresh()
            live_broker._ensure_reader(self.device.pk)
            self.assertTrue(self._wait(lambda: api.reads >= 2))
            # Simulate another process: same lock namespace, fresh thread.
            live_broker._threads.pop(self.device.pk, None)
            live_broker._ensure_reader(self.device.pk)
            time.sleep(0.4)
        self.assertEqual(self.connections, 1, "a second reader opened a second connection")

    def test_rates_are_published(self):
        conn_patch, read_patch, api = self._patched()
        with conn_patch, read_patch, mock.patch.object(live_broker, "POLL_SECONDS", 0.05):
            self._fresh()
            live_broker._ensure_reader(self.device.pk)
            self.assertTrue(self._wait(
                lambda: RouterLiveRate.objects.filter(username="pppoe_test", download_bps__gt=0).exists()
            ), "no rate was ever published")
        row = RouterLiveRate.objects.get(username="pppoe_test")
        self.assertGreater(row.download_bps, 0)
        self.assertGreater(row.upload_bps, 0)
        self.assertEqual(RouterLiveRate.objects.count(), 1, "upsert, not append")

    def test_first_reading_reports_no_rate(self):
        """A first sighting has no baseline; a rate there would be a session's
        whole lifetime divided by one interval."""

        def stop_after_this_read(_api):
            # Expire interest during the first read so the loop does exactly
            # one iteration -- deterministic, instead of racing a long sleep.
            LiveTrafficInterest.objects.filter(device=self.device).update(
                last_requested_at=timezone.now() - timezone.timedelta(hours=1)
            )

        conn_patch, read_patch, api = self._patched(on_read=stop_after_this_read)
        with conn_patch, read_patch, mock.patch.object(live_broker, "POLL_SECONDS", 0.05):
            self._fresh()
            live_broker._ensure_reader(self.device.pk)
            self.assertTrue(self._wait(lambda: RouterLiveRate.objects.exists()))
            self.assertTrue(self._wait(lambda: not live_broker._threads))
        self.assertEqual(api.reads, 1, "the loop should have run exactly once")
        row = RouterLiveRate.objects.get()
        self.assertEqual((row.download_bps, row.upload_bps), (0, 0))

    def test_an_unreachable_router_does_not_crash_the_thread(self):
        def boom(device, timeout=8):
            raise mikrotik.MikrotikError("couldn't reach it")

        with mock.patch.object(mikrotik, "api_connection", boom):
            self._fresh()
            live_broker._ensure_reader(self.device.pk)
            self.assertTrue(self._wait(lambda: not live_broker._threads, timeout=5))

    # ---- the signal itself ----------------------------------------------

    def test_asking_for_usage_registers_interest(self):
        tariff = Tariff.objects.create(name="T", price=Decimal("1"),
                                       speed_download_kbps=1024, speed_upload_kbps=1024)
        customer = Customer.objects.create(full_name="Watch Wendy", email="w@x.com")
        Service.objects.create(customer=customer, tariff=tariff, status=Service.Status.ACTIVE,
                               start_date="2026-08-01", radius_username="wendy",
                               device=self.device)
        with mock.patch.object(live_broker, "_ensure_reader") as ensure:
            self.assertEqual(request_live_readings(customer), 1)
        ensure.assert_called_once_with(self.device.pk)
        self.assertTrue(LiveTrafficInterest.objects.filter(device=self.device).exists())

    def test_a_terminated_service_does_not_wake_a_router(self):
        tariff = Tariff.objects.create(name="T2", price=Decimal("1"),
                                       speed_download_kbps=1024, speed_upload_kbps=1024)
        customer = Customer.objects.create(full_name="Gone Gary", email="g@x.com")
        Service.objects.create(customer=customer, tariff=tariff, status=Service.Status.TERMINATED,
                               start_date="2026-08-01", radius_username="gary", device=self.device)
        with mock.patch.object(live_broker, "_ensure_reader") as ensure:
            self.assertEqual(request_live_readings(customer), 0)
        ensure.assert_not_called()


class PublicLiveToggleTests(TransactionTestCase):
    """Staff decide, per customer, whether the no-login usage link shows the
    live graph -- because that graph is what holds a router connection open,
    and the link needs no login."""

    reset_sequences = True

    def setUp(self):
        from rest_framework.test import APIRequestFactory
        self.factory = APIRequestFactory()
        self.device = Device.objects.create(
            name="Bench", ip_address="10.0.0.1", api_enabled=True,
            api_username="u", api_password="p",
        )
        tariff = Tariff.objects.create(name="T", price=Decimal("1"),
                                       speed_download_kbps=1024, speed_upload_kbps=1024)
        self.customer = Customer.objects.create(full_name="Public Pam", email="p@x.com")
        Service.objects.create(customer=self.customer, tariff=tariff,
                               status=Service.Status.ACTIVE, start_date="2026-08-01",
                               radius_username="pam", device=self.device)
        live_broker._threads.clear()

    def _public_get(self):
        from radiusauth.views import PublicUsageView
        request = self.factory.get("/x", {"period": "month"})
        return PublicUsageView.as_view()(request, token=str(self.customer.usage_token))

    def test_off_by_default_no_router_is_touched(self):
        with mock.patch.object(live_broker, "_ensure_reader") as ensure:
            response = self._public_get()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["live_bandwidth_enabled"])
        ensure.assert_not_called()

    def test_on_the_public_page_wakes_the_router(self):
        self.customer.live_bandwidth_public = True
        self.customer.save()
        with mock.patch.object(live_broker, "_ensure_reader") as ensure:
            response = self._public_get()
        self.assertTrue(response.data["live_bandwidth_enabled"])
        ensure.assert_called_once_with(self.device.pk)

    def test_staff_can_flip_it(self):
        from django.contrib.auth import get_user_model
        from rest_framework.test import force_authenticate
        from customers.views import CustomerViewSet

        admin = get_user_model().objects.create_user(username="boss", password="x", role="admin")
        request = self.factory.patch("/x", {"live_bandwidth_public": True}, format="json")
        force_authenticate(request, user=admin)
        response = CustomerViewSet.as_view({"patch": "partial_update"})(request, pk=self.customer.pk)
        self.assertEqual(response.status_code, 200)
        self.customer.refresh_from_db()
        self.assertTrue(self.customer.live_bandwidth_public)


class PortalLiveTests(TransactionTestCase):
    """A signed-in customer watching their own line.

    The bug this covers: the fast per-second poll went into the staff card
    only, and the endpoint behind it was staff-only too -- so the portal
    showed a figure that updated every ten seconds and looked stuck.
    """

    reset_sequences = True

    def setUp(self):
        from django.contrib.auth import get_user_model
        from rest_framework.test import APIRequestFactory

        self.factory = APIRequestFactory()
        self.device = Device.objects.create(
            name="Bench", ip_address="10.0.0.1", api_enabled=True,
            api_username="u", api_password="p",
        )
        tariff = Tariff.objects.create(name="T", price=Decimal("1"),
                                       speed_download_kbps=1024, speed_upload_kbps=1024)
        self.customer = Customer.objects.create(full_name="Portal Pete", email="pete@x.com")
        Service.objects.create(customer=self.customer, tariff=tariff,
                               status=Service.Status.ACTIVE, start_date="2026-08-01",
                               radius_username="pete", device=self.device)
        User = get_user_model()
        self.user = User.objects.create_user(username="pete", password="x", role="customer")
        self.customer.user = self.user
        self.customer.save()
        self.other = Customer.objects.create(full_name="Someone Else", email="e@x.com")
        live_broker._threads.clear()

    def _live(self, pk, user):
        from rest_framework.test import force_authenticate
        from radiusauth.views import CustomerLiveRateView

        request = self.factory.get("/x")
        force_authenticate(request, user=user)
        return CustomerLiveRateView.as_view()(request, pk=pk)

    def test_customer_gets_live_when_staff_enabled_it(self):
        self.customer.live_bandwidth_public = True
        self.customer.save()
        with mock.patch.object(live_broker, "_ensure_reader") as ensure:
            response = self._live(self.customer.pk, self.user)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["live_enabled"])
        ensure.assert_called_once_with(self.device.pk)

    def test_customer_gets_nothing_and_wakes_no_router_when_off(self):
        with mock.patch.object(live_broker, "_ensure_reader") as ensure:
            response = self._live(self.customer.pk, self.user)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["live_enabled"])
        self.assertEqual(response.data["live_sessions"], [])
        ensure.assert_not_called()

    def test_a_customer_cannot_watch_somebody_else(self):
        self.other.live_bandwidth_public = True
        self.other.save()
        response = self._live(self.other.pk, self.user)
        self.assertEqual(response.status_code, 404)

    def test_staff_are_not_gated_by_the_toggle(self):
        from django.contrib.auth import get_user_model
        admin = get_user_model().objects.create_user(username="boss", password="x", role="admin")
        self.assertFalse(self.customer.live_bandwidth_public)
        with mock.patch.object(live_broker, "_ensure_reader") as ensure:
            response = self._live(self.customer.pk, admin)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["live_enabled"])
        ensure.assert_called_once_with(self.device.pk)

    def test_the_slow_usage_endpoint_does_not_wake_a_router_when_off(self):
        from rest_framework.test import force_authenticate
        from radiusauth.views import CustomerUsageView

        request = self.factory.get("/x")
        force_authenticate(request, user=self.user)
        with mock.patch.object(live_broker, "_ensure_reader") as ensure:
            response = CustomerUsageView.as_view()(request, pk=self.customer.pk)
        self.assertEqual(response.status_code, 200)
        ensure.assert_not_called()


class LiveBandwidthExpiryTests(TransactionTestCase):
    """The grant turns itself off after five idle minutes.

    A switch only a human can turn off is a switch that stays on. This one is
    normally flipped to help somebody debug their line for a few minutes; left
    on, it is a customer able to hold a router connection open at will, months
    after anyone remembers enabling it.
    """

    reset_sequences = True

    def setUp(self):
        from django.contrib.auth import get_user_model
        from rest_framework.test import APIRequestFactory

        self.factory = APIRequestFactory()
        self.device = Device.objects.create(
            name="Bench", ip_address="10.0.0.1", api_enabled=True,
            api_username="u", api_password="p",
        )
        tariff = Tariff.objects.create(name="T", price=Decimal("1"),
                                       speed_download_kbps=1024, speed_upload_kbps=1024)
        self.customer = Customer.objects.create(
            full_name="Idle Ida", email="ida@x.com", live_bandwidth_public=True,
            live_bandwidth_last_viewed_at=timezone.now(),
        )
        Service.objects.create(customer=self.customer, tariff=tariff,
                               status=Service.Status.ACTIVE, start_date="2026-08-01",
                               radius_username="ida", device=self.device)
        self.user = get_user_model().objects.create_user(
            username="ida", password="x", role="customer"
        )
        self.customer.user = self.user
        self.customer.save()
        live_broker._threads.clear()

    def _idle_for(self, minutes):
        Customer.objects.filter(pk=self.customer.pk).update(
            live_bandwidth_last_viewed_at=timezone.now() - timezone.timedelta(minutes=minutes)
        )
        self.customer.refresh_from_db()

    def _live(self, user=None):
        from rest_framework.test import force_authenticate
        from radiusauth.views import CustomerLiveRateView

        request = self.factory.get("/x")
        force_authenticate(request, user=user or self.user)
        return CustomerLiveRateView.as_view()(request, pk=self.customer.pk)

    def test_it_survives_while_being_watched(self):
        self._idle_for(2)
        with mock.patch.object(live_broker, "_ensure_reader"):
            self.assertTrue(self._live().data["live_enabled"])
        self.customer.refresh_from_db()
        self.assertTrue(self.customer.live_bandwidth_public)

    def test_it_switches_itself_off_after_five_idle_minutes(self):
        self._idle_for(6)
        with mock.patch.object(live_broker, "_ensure_reader") as ensure:
            response = self._live()
        self.assertFalse(response.data["live_enabled"])
        ensure.assert_not_called()
        self.customer.refresh_from_db()
        self.assertFalse(self.customer.live_bandwidth_public,
                         "the grant should have been switched off, not just refused")

    def test_watching_pushes_the_deadline_out(self):
        self._idle_for(4)
        with mock.patch.object(live_broker, "_ensure_reader"):
            self._live()
        self.customer.refresh_from_db()
        self.assertLess(
            (timezone.now() - self.customer.live_bandwidth_last_viewed_at).total_seconds(), 5,
            "a live view should have reset the idle clock",
        )

    def test_the_clock_is_not_written_on_every_poll(self):
        """The page polls once a second; a row write per second to keep a
        five-minute timer accurate is a poor trade."""
        self.customer.touch_live_bandwidth_view()
        first = Customer.objects.get(pk=self.customer.pk).live_bandwidth_last_viewed_at
        self.customer.touch_live_bandwidth_view()
        self.assertEqual(Customer.objects.get(pk=self.customer.pk).live_bandwidth_last_viewed_at, first)

    def test_the_public_link_expires_it_too(self):
        from radiusauth.views import PublicUsageView

        self._idle_for(6)
        request = self.factory.get("/x", {"period": "month"})
        with mock.patch.object(live_broker, "_ensure_reader") as ensure:
            response = PublicUsageView.as_view()(request, token=str(self.customer.usage_token))
        self.assertFalse(response.data["live_bandwidth_enabled"])
        ensure.assert_not_called()
        self.customer.refresh_from_db()
        self.assertFalse(self.customer.live_bandwidth_public)

    def test_staff_see_the_true_state_on_the_customer_page(self):
        from django.contrib.auth import get_user_model
        from rest_framework.test import force_authenticate
        from customers.views import CustomerViewSet

        self._idle_for(6)
        admin = get_user_model().objects.create_user(username="boss", password="x", role="admin")
        request = self.factory.get("/x")
        force_authenticate(request, user=admin)
        response = CustomerViewSet.as_view({"get": "retrieve"})(request, pk=self.customer.pk)
        self.assertFalse(response.data["live_bandwidth_public"],
                         "the toggle should not read On for access that has already lapsed")

    def test_enabling_it_starts_the_clock(self):
        """Turned on and never used, it should still expire five minutes
        later rather than sitting on forever."""
        from django.contrib.auth import get_user_model
        from rest_framework.test import force_authenticate
        from customers.views import CustomerViewSet

        Customer.objects.filter(pk=self.customer.pk).update(
            live_bandwidth_public=False, live_bandwidth_last_viewed_at=None
        )
        admin = get_user_model().objects.create_user(username="boss2", password="x", role="admin")
        request = self.factory.patch("/x", {"live_bandwidth_public": True}, format="json")
        force_authenticate(request, user=admin)
        CustomerViewSet.as_view({"patch": "partial_update"})(request, pk=self.customer.pk)
        self.customer.refresh_from_db()
        self.assertIsNotNone(self.customer.live_bandwidth_last_viewed_at)

    def test_staff_are_never_expired_out(self):
        from django.contrib.auth import get_user_model

        self._idle_for(60)
        admin = get_user_model().objects.create_user(username="boss3", password="x", role="admin")
        with mock.patch.object(live_broker, "_ensure_reader") as ensure:
            response = self._live(user=admin)
        self.assertTrue(response.data["live_enabled"])
        ensure.assert_called_once()
