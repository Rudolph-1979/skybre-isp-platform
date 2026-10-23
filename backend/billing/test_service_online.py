"""Online / Offline per service line on the client's Services table
(Rudolph, 8 Oct 2026: "Vuma WB is online, the Vuma 100/50 went offline. I
need to see online status right hand side of Active Status").
"""
import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from billing.models import Service, Tariff
from customers.models import Customer
from radiusauth.models import RadAcct

User = get_user_model()


class ServiceOnlineTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_user(username="boss", password="x", role="admin"))
        tariff = Tariff.objects.create(name="Vuma", price=100, speed_download_kbps=1024, speed_upload_kbps=1024)
        self.customer = Customer.objects.create(full_name="Elzane Meyer", email="em@example.com",
                                                status=Customer.Status.ACTIVE)

        def line(username, status=Service.Status.ACTIVE):
            return Service.objects.create(customer=self.customer, tariff=tariff, status=status,
                                          start_date="2026-10-05", radius_username=username)

        self.up = line("bup_krd_elzane_meyer")
        self.down = line("svf_krd_elzane_meyer")
        self.no_login = line("")
        self.suspended = line("sus_krd_elzane_meyer", status=Service.Status.SUSPENDED)

        def session(username, start_ago, update_ago, stop_ago=None):
            RadAcct.objects.create(
                acctsessionid=f"{username}-{start_ago}", acctuniqueid=f"{username}-{start_ago}",
                username=username, nasipaddress="10.0.0.1",
                acctstarttime=self.now - datetime.timedelta(minutes=start_ago),
                acctupdatetime=self.now - datetime.timedelta(minutes=update_ago),
                acctstoptime=self.now - datetime.timedelta(minutes=stop_ago) if stop_ago is not None else None,
            )

        session("bup_krd_elzane_meyer", 600, 2)            # open, heard from 2 min ago
        session("svf_krd_elzane_meyer", 600, 120, 90)      # stopped 90 min ago
        session("sus_krd_elzane_meyer", 600, 2)            # suspended: not reported

    def _rows(self):
        res = self.client.get(f"/api/services/?customer={self.customer.id}")
        self.assertEqual(res.status_code, 200, res.data)
        return {row["id"]: row for row in res.data["results"]}

    def test_each_line_has_its_own_state(self):
        rows = self._rows()
        self.assertIs(rows[self.up.id]["online"], True)
        self.assertIs(rows[self.down.id]["online"], False)
        self.assertIsNotNone(rows[self.down.id]["last_seen_at"])

    def test_no_login_or_not_active_reports_nothing(self):
        rows = self._rows()
        self.assertIsNone(rows[self.no_login.id]["online"])
        self.assertIsNone(rows[self.suspended.id]["online"])

    def test_a_session_that_stopped_reporting_is_offline(self):
        RadAcct.objects.filter(username="bup_krd_elzane_meyer").update(
            acctupdatetime=self.now - datetime.timedelta(hours=2))
        self.assertIs(self._rows()[self.up.id]["online"], False)

    def test_single_service_has_it_too(self):
        res = self.client.get(f"/api/services/{self.up.id}/")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertIs(res.data["online"], True)
