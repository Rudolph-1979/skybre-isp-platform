"""The Live Sessions table.

The bug: DURATION read acctsessiontime and IN/OUT read the accounting byte
counters. FreeRADIUS only writes those on an Interim-Update, and this NAS is
on interim-update=5m -- so every session read "0m" and "0.0 MB / 0.0 MB" for
its first five minutes and then jumped.
"""
import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from network.models import Device
from radiusauth.models import RadAcct, RouterLiveRate
from radiusauth.views import RadAcctViewSet

User = get_user_model()
MB = 1024 * 1024


class LiveSessionTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username="boss", password="x", role="admin")
        self.device = Device.objects.create(
            name="Bench", ip_address="10.0.0.1", api_enabled=True,
            api_username="u", api_password="p",
        )
        self.started = timezone.now() - datetime.timedelta(minutes=3)
        self.session = RadAcct.objects.create(
            acctsessionid="s1", acctuniqueid="s1", username="pppoe_test",
            nasipaddress="10.0.0.1", acctstarttime=self.started,
            # Exactly the state the screenshot showed: an Accounting-Start has
            # landed and no interim has followed it yet.
            acctsessiontime=None, acctinputoctets=0, acctoutputoctets=0,
            framedipaddress="102.23.154.3",
        )

    def _list(self):
        from unittest import mock
        request = APIRequestFactory().get("/x", {"active_only": "true"})
        force_authenticate(request, user=self.staff)
        with mock.patch("network.live_broker.register_interest"):
            return RadAcctViewSet.as_view({"get": "list"})(request)

    def test_duration_is_real_not_the_last_reported_value(self):
        row = self._list().data["results"][0]
        self.assertIsNotNone(row["duration_seconds"])
        self.assertAlmostEqual(row["duration_seconds"], 180, delta=10)

    def test_a_finished_session_measures_start_to_stop(self):
        self.session.acctstoptime = self.started + datetime.timedelta(minutes=90)
        self.session.save()
        request = APIRequestFactory().get("/x")
        force_authenticate(request, user=self.staff)
        from unittest import mock
        with mock.patch("network.live_broker.register_interest"):
            response = RadAcctViewSet.as_view({"get": "list"})(request)
        self.assertEqual(response.data["results"][0]["duration_seconds"], 90 * 60)

    def test_router_counters_are_used_when_fresh(self):
        RouterLiveRate.objects.create(
            username="pppoe_test", device=self.device, interface="<pppoe-pppoe_test>",
            last_rx_byte=3 * MB, last_tx_byte=40 * MB,
            download_bps=0, upload_bps=0, sampled_at=timezone.now(),
        )
        row = self._list().data["results"][0]
        self.assertEqual(row["live_input_octets"], 3 * MB)
        self.assertEqual(row["live_output_octets"], 40 * MB)

    def test_a_stale_router_reading_is_not_offered_as_live(self):
        RouterLiveRate.objects.create(
            username="pppoe_test", device=self.device, interface="<pppoe-pppoe_test>",
            last_rx_byte=3 * MB, last_tx_byte=40 * MB,
            download_bps=0, upload_bps=0,
            sampled_at=timezone.now() - datetime.timedelta(minutes=10),
        )
        row = self._list().data["results"][0]
        self.assertIsNone(row["live_input_octets"],
                          "a ten-minute-old reading must not be presented as current")

    def test_opening_the_page_wakes_the_live_reader(self):
        from unittest import mock
        request = APIRequestFactory().get("/x", {"active_only": "true"})
        force_authenticate(request, user=self.staff)
        with mock.patch("network.live_broker.register_interest") as register:
            RadAcctViewSet.as_view({"get": "list"})(request)
        register.assert_called_once_with(self.device.pk)
