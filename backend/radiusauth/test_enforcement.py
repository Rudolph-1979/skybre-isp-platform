"""End to end: a Service save has to reach a live session.

A fake NAS stands in for the router, validating both signatures exactly as
RouterOS does, so these assert the real wire behaviour rather than that a
mock was called.
"""
import datetime
import threading
from decimal import Decimal
from unittest import mock

from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from billing.models import Service, Tariff
from customers.models import Customer
from network.models import Device
from radiusauth import dynauth, enforcement
from radiusauth.fake_nas_testing import FakeNAS
from radiusauth.models import RadAcct, RadiusAction, RadiusNasClient

SECRET = "testing-secret-not-real"
NAS_IP = "127.0.0.1"


class EnforcementTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.tariff = Tariff.objects.create(
            name="4 Mbps", price=Decimal("499"), speed_download_kbps=4096, speed_upload_kbps=4096
        )
        self.faster = Tariff.objects.create(
            name="20 Mbps", price=Decimal("899"), speed_download_kbps=20480, speed_upload_kbps=5120
        )
        self.device = Device.objects.create(name="Bench", ip_address=NAS_IP, api_enabled=False)
        self.customer = Customer.objects.create(full_name="Live Larry", email="l@x.com")
        self.nas = RadiusNasClient.objects.create(
            name="Bench", ip_address=NAS_IP, shortname="bench", secret=SECRET
        )
        self.service = Service.objects.create(
            customer=self.customer, tariff=self.tariff, status=Service.Status.ACTIVE,
            start_date="2026-08-01", radius_connection_type="pppoe",
            radius_username="pppoe_test", radius_password="pw", device=self.device,
            ip_assignment_mode="manual", static_ip="102.23.154.3",
        )

    def _session(self, seconds_ago=10):
        now = timezone.now()
        return RadAcct.objects.create(
            acctsessionid="81f0a2c4", acctuniqueid="u1", username="pppoe_test",
            nasipaddress=NAS_IP, acctstarttime=now - datetime.timedelta(minutes=30),
            acctupdatetime=now - datetime.timedelta(seconds=seconds_ago),
            framedipaddress="102.23.154.3",
        )

    # ---- the headline behaviour -----------------------------------------

    def test_speed_change_uses_coa_and_does_not_disconnect(self):
        self._session()
        nas = FakeNAS(SECRET)
        with mock.patch.object(dynauth, "DEFAULT_PORT", nas.port), \
             mock.patch.object(dynauth, "coa_port", lambda: nas.port):
            self.service.tariff = self.faster
            enforcement.apply_change(self.service, "tariff")

        self.assertEqual(len(nas.received), 1)
        packet = nas.received[0]
        self.assertEqual(packet["code"], dynauth.CODE_COA_REQUEST, "must be a CoA, not a disconnect")
        self.assertTrue(packet["req_auth_ok"])
        self.assertTrue(packet["msg_auth_ok"])
        vsa = [a for a in nas.parse_attrs(packet["payload"]) if a[0] == "VSA"][0]
        self.assertEqual((vsa[1], vsa[2]), (14988, 8))
        self.assertEqual(vsa[3].decode(), "5120k/20480k")

        action = RadiusAction.objects.first()
        self.assertTrue(action.ok)
        self.assertEqual((action.action, action.transport), ("coa_rate", "coa"))
        self.assertIn("stayed connected", action.detail)

    def test_status_change_disconnects(self):
        self._session()
        nas = FakeNAS(SECRET)
        with mock.patch.object(dynauth, "coa_port", lambda: nas.port):
            self.service.status = Service.Status.SUSPENDED
            enforcement.apply_change(self.service, "status")
        self.assertEqual(nas.received[0]["code"], dynauth.CODE_DISCONNECT_REQUEST)
        self.assertTrue(RadiusAction.objects.first().ok)

    def test_session_is_identified_by_acct_session_id(self):
        """User-Name alone is ambiguous once a customer has two lines."""
        self._session()
        nas = FakeNAS(SECRET)
        with mock.patch.object(dynauth, "coa_port", lambda: nas.port):
            enforcement.apply_change(self.service, "status")
        attrs = dict((a[0], a[1]) for a in nas.parse_attrs(nas.received[0]["payload"]) if a[0] != "VSA")
        self.assertEqual(attrs[dynauth.ATTR_USER_NAME], b"pppoe_test")
        self.assertEqual(attrs[dynauth.ATTR_ACCT_SESSION_ID], b"81f0a2c4")

    # ---- the failures that used to be invisible ---------------------------

    def test_router_not_listening_is_recorded_as_a_failure(self):
        self._session()
        nas = FakeNAS(SECRET, behaviour="silent")
        with mock.patch.object(dynauth, "coa_port", lambda: nas.port):
            ok = enforcement.apply_change(self.service, "status")
        self.assertFalse(ok)
        action = RadiusAction.objects.first()
        self.assertFalse(action.ok)
        self.assertIn("OLD settings", action.detail)

    def test_wrong_secret_is_named_as_such(self):
        self._session()
        nas = FakeNAS("a-completely-different-secret")
        with mock.patch.object(dynauth, "coa_port", lambda: nas.port):
            enforcement.apply_change(self.service, "status")
        self.assertIn("secret", RadiusAction.objects.first().detail.lower())

    def test_nak_reason_is_reported(self):
        self._session()
        nas = FakeNAS(SECRET, behaviour="nak", error_cause=503)
        with mock.patch.object(dynauth, "coa_port", lambda: nas.port):
            enforcement.apply_change(self.service, "status")
        self.assertIn("Session context not found", RadiusAction.objects.first().detail)

    def test_no_nas_record_says_so(self):
        self._session()
        RadiusNasClient.objects.all().delete()
        enforcement.apply_change(self.service, "status")
        action = RadiusAction.objects.first()
        self.assertFalse(action.ok)
        self.assertIn("shared secret", action.detail)

    # ---- the quiet, correct cases ----------------------------------------

    def test_no_live_session_is_success_not_failure(self):
        ok = enforcement.apply_change(self.service, "tariff")
        self.assertTrue(ok)
        action = RadiusAction.objects.first()
        self.assertTrue(action.ok)
        self.assertEqual(action.action, "none")
        self.assertIn("next connection", action.detail)

    def test_a_stale_accounting_row_is_not_treated_as_live(self):
        self._session(seconds_ago=enforcement.STALE_SESSION_SECONDS + 60)
        ok = enforcement.apply_change(self.service, "tariff")
        self.assertTrue(ok)
        self.assertEqual(RadiusAction.objects.first().action, "none")

    def test_api_fallback_when_coa_fails(self):
        self._session()
        nas = FakeNAS(SECRET, behaviour="silent")
        with mock.patch.object(dynauth, "coa_port", lambda: nas.port), \
             mock.patch("network.router_sync.disconnect_service_sessions", return_value=1):
            ok = enforcement.apply_change(self.service, "tariff")
        self.assertTrue(ok)
        action = RadiusAction.objects.first()
        self.assertEqual(action.transport, "api")
        self.assertIn("dropping the session instead", action.detail)


class SignalWiringTests(TestCase):
    """The signal has to actually call the new path -- a correct enforcement
    module that nothing invokes is exactly the bug being fixed.

    Two things make this awkward and both are real, not test artefacts: the
    call is registered with transaction.on_commit (so captureOnCommitCallbacks
    is needed, or it is rolled back unrun), and it then runs in a daemon
    thread (so the assertion has to wait for it rather than assume it has
    already happened).
    """

    def setUp(self):
        self.tariff = Tariff.objects.create(name="A", price=Decimal("1"), speed_download_kbps=4096, speed_upload_kbps=4096)
        self.other = Tariff.objects.create(name="B", price=Decimal("2"), speed_download_kbps=8192, speed_upload_kbps=8192)
        self.customer = Customer.objects.create(full_name="Sig Sam", email="s@x.com")
        self.service = Service.objects.create(
            customer=self.customer, tariff=self.tariff, status=Service.Status.ACTIVE,
            start_date="2026-08-01", radius_username="sig_sam", radius_password="pw",
        )

    def _capture(self, mutate):
        """Returns the (service, reason) the signal pushed, or None."""
        fired = threading.Event()
        captured = {}

        def spy(service, reason):
            captured["args"] = (service, reason)
            fired.set()
            return True

        with mock.patch("radiusauth.enforcement.apply_change", side_effect=spy):
            with self.captureOnCommitCallbacks(execute=True):
                mutate()
            fired.wait(timeout=5)
        return captured.get("args")

    def test_tariff_change_calls_enforcement_with_tariff(self):
        def mutate():
            self.service.tariff = self.other
            self.service.save()

        args = self._capture(mutate)
        self.assertIsNotNone(args, "the tariff change never reached enforcement")
        self.assertEqual(args[1], "tariff")

    def test_status_change_calls_enforcement_with_status(self):
        def mutate():
            self.service.status = Service.Status.SUSPENDED
            self.service.save()

        args = self._capture(mutate)
        self.assertIsNotNone(args, "the status change never reached enforcement")
        self.assertEqual(args[1], "status")

    def test_an_unchanged_save_pushes_nothing(self):
        self.assertIsNone(self._capture(lambda: self.service.save()))
