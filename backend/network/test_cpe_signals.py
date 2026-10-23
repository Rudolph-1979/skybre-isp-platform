"""Client CPE signal from the MikroTik APs (Rudolph, 8 Oct 2026: "In the
client services I would like to read client mikrotik and ubnt cpe
signals")."""
import datetime
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from billing.models import Service, Tariff
from customers.models import Customer
from network import cpe_signals
from network.mikrotik import MikrotikError
from network.models import CpeSignal, CpeSignalSample, Device
from radiusauth.models import RadAcct

User = get_user_model()

# As RouterOS's legacy wireless registration table returns it, for the
# "Gaggle In" row on 10.200.6.7 in Rudolph's Winbox screenshot.
GAGGLE_ROW = {
    "interface": "wlan1", "radio-name": "Gaggle In", "mac-address": "B8:69:F4:61:3C:BB",
    "uptime": "41m22s", "distance": "4", "signal-strength": "-50@HT20-7", "tx-signal-strength": "-49",
    "signal-to-noise": "56", "tx-ccq": "73", "rx-ccq": "92",
    "tx-rate": "115.5Mbps-20MHz/2S/SGI", "rx-rate": "144.4Mbps-20MHz/2S/SGI",
}


class CpeSignalTests(TestCase):
    def setUp(self):
        self.ap = Device.objects.create(name="KRD AP", device_type="access_point", ip_address="10.200.6.7",
                                        api_enabled=True, api_username="api", api_password="x")
        self.dead = Device.objects.create(name="Dead AP", device_type="access_point", ip_address="10.200.9.9",
                                          api_enabled=True, api_username="api", api_password="x")
        tariff = Tariff.objects.create(name="T", price=100, speed_download_kbps=1024, speed_upload_kbps=1024)
        self.customer = Customer.objects.create(full_name="Gaggle Inn", email="g@example.com")

        def line(login):
            return Service.objects.create(customer=self.customer, tariff=tariff, status=Service.Status.ACTIVE,
                                          start_date="2026-10-01", radius_username=login)

        self.gaggle = line("krd_gaggle_inn")
        self.elsewhere = line("krd_elsewhere")
        self.fresh = line("krd_never_connected")
        now = timezone.now()
        # An older session with another MAC: the newest one wins.
        RadAcct.objects.create(acctsessionid="a0", acctuniqueid="a0", username="krd_gaggle_inn",
                               nasipaddress="10.0.0.1", acctstarttime=now - datetime.timedelta(days=9),
                               callingstationid="AA:AA:AA:AA:AA:AA")
        RadAcct.objects.create(acctsessionid="a1", acctuniqueid="a1", username="krd_gaggle_inn",
                               nasipaddress="10.0.0.1", acctstarttime=now - datetime.timedelta(hours=1),
                               callingstationid="b8-69-f4-61-3c-bb")
        RadAcct.objects.create(acctsessionid="a2", acctuniqueid="a2", username="krd_elsewhere",
                               nasipaddress="10.0.0.1", acctstarttime=now - datetime.timedelta(hours=1),
                               callingstationid="44:D9:E7:5A:AC:98")

    def _read(self, device):
        if device.pk == self.dead.pk:
            raise MikrotikError("Dead AP: couldn't reach 10.200.9.9:8728")
        return [cpe_signals.parse_row(GAGGLE_ROW, device)]

    def test_parse_row_from_winbox_values(self):
        row = cpe_signals.parse_row(GAGGLE_ROW, self.ap)
        self.assertEqual(row["mac"], "B8:69:F4:61:3C:BB")
        self.assertEqual((row["signal_dbm"], row["tx_signal_dbm"]), (-50, -49))
        self.assertEqual((row["tx_ccq"], row["rx_ccq"], row["snr_db"]), (73, 92, 56))
        self.assertEqual(row["radio_name"], "Gaggle In")
        blank = cpe_signals.parse_row({"mac-address": "x"}, self.ap)
        self.assertEqual(blank["mac"], "")
        self.assertIsNone(blank["signal_dbm"])

    def test_poll_matches_by_radius_mac_and_survives_a_dead_ap(self):
        with mock.patch.object(cpe_signals, "read_registration", side_effect=self._read):
            result = cpe_signals.poll_all()
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["matched"], 1)
        gaggle = CpeSignal.objects.get(service=self.gaggle)
        self.assertEqual(gaggle.state, "ok")
        self.assertEqual(gaggle.ap, self.ap)
        self.assertEqual(gaggle.signal_dbm, -50)
        self.assertEqual(CpeSignal.objects.get(service=self.elsewhere).state, "not_seen")
        self.assertEqual(CpeSignal.objects.get(service=self.fresh).state, "no_mac")
        self.assertEqual(CpeSignalSample.objects.filter(service=self.gaggle).count(), 1)

    def test_last_reading_kept_when_cpe_disappears(self):
        with mock.patch.object(cpe_signals, "read_registration", side_effect=self._read):
            cpe_signals.poll_all()
        with mock.patch.object(cpe_signals, "read_registration", return_value=[]):
            cpe_signals.poll_all()
        gaggle = CpeSignal.objects.get(service=self.gaggle)
        self.assertEqual(gaggle.state, "not_seen")
        self.assertEqual(gaggle.signal_dbm, -50)
        self.assertIsNotNone(gaggle.seen_at)

    def test_router_without_wireless_is_skipped(self):
        class FakeApi:
            def __call__(self, path):
                raise Exception("no such command prefix")

        class Ctx:
            def __enter__(self):
                return FakeApi()

            def __exit__(self, *a):
                return False

        with mock.patch("network.mikrotik.api_connection", return_value=Ctx()):
            self.assertEqual(cpe_signals.read_registration(self.ap), [])

    def test_api_list_history_refresh(self):
        client = APIClient()
        client.force_authenticate(User.objects.create_user(username="boss", password="x", role="admin"))
        with mock.patch.object(cpe_signals, "read_registration", side_effect=self._read):
            cpe_signals.poll_all()
            res = client.get(f"/api/cpe-signals/?customer={self.customer.id}")
            self.assertEqual(res.status_code, 200, res.data)
            rows = {r["service"]: r for r in res.data["results"]}
            self.assertEqual(rows[self.gaggle.id]["ap_name"], "KRD AP")
            self.assertEqual(rows[self.gaggle.id]["signal_dbm"], -50)
            hist = client.get(f"/api/cpe-signals/{self.gaggle.id}/history/?hours=24")
            self.assertEqual(len(hist.data["results"]), 1)
            ref = client.post(f"/api/cpe-signals/{self.gaggle.id}/refresh/")
            self.assertEqual(ref.status_code, 200, ref.data)
            self.assertEqual(ref.data["state"], "ok")
        self.assertEqual(client.get("/api/cpe-signals/").status_code, 400)
