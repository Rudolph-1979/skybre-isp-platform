"""Customers list Offline filter with a client whose lines disagree
(Rudolph, 8 Oct 2026: "Elzane Meyer with 1 offline service does not show in
the offline status?"). One line down is enough for Offline; the client is
still Online on the other, and the Connection column says how many are down.
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


class PartialOfflineTests(TestCase):
    def setUp(self):
        now = timezone.now()
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_user(username="boss", password="x", role="admin"))
        tariff = Tariff.objects.create(name="Vuma", price=100, speed_download_kbps=1024, speed_upload_kbps=1024)

        def customer(name, *logins):
            c = Customer.objects.create(full_name=name, email=f"{name.split()[0].lower()}@example.com",
                                        status=Customer.Status.ACTIVE)
            for login in logins:
                Service.objects.create(customer=c, tariff=tariff, status=Service.Status.ACTIVE,
                                       start_date="2026-10-05", radius_username=login)
            return c

        def up(login):
            RadAcct.objects.create(acctsessionid=login, acctuniqueid=login, username=login, nasipaddress="10.0.0.1",
                                   acctstarttime=now - datetime.timedelta(hours=5),
                                   acctupdatetime=now - datetime.timedelta(minutes=2))

        self.mixed = customer("Elzane Meyer", "bup_elzane", "svf_elzane")
        self.all_up = customer("Up Only", "up_only")
        self.all_down = customer("Down Only", "down_only")
        self.no_line = customer("Elsewhere")
        up("bup_elzane")
        up("up_only")

    def _ids(self, connection):
        res = self.client.get(f"/api/customers/?connection={connection}&page_size=100")
        self.assertEqual(res.status_code, 200, res.data)
        return {row["id"] for row in res.data["results"]}

    def test_offline_includes_a_client_with_one_line_down(self):
        self.assertEqual(self._ids("offline"), {self.mixed.id, self.all_down.id})

    def test_online_still_includes_them(self):
        self.assertEqual(self._ids("online"), {self.mixed.id, self.all_up.id})

    def test_none_is_unchanged(self):
        self.assertEqual(self._ids("none"), {self.no_line.id})

    def test_column_counts_the_lines_down(self):
        res = self.client.get("/api/customers/?page_size=100")
        rows = {row["id"]: row for row in res.data["results"]}
        self.assertIs(rows[self.mixed.id]["online"], True)
        self.assertEqual(rows[self.mixed.id]["offline_lines"], 1)
        self.assertEqual(rows[self.all_up.id]["offline_lines"], 0)
        self.assertIs(rows[self.all_down.id]["online"], False)
        self.assertIsNone(rows[self.no_line.id]["offline_lines"])

    def test_dashboard_counts_offline_lines_and_their_clients(self):
        res = self.client.get("/api/dashboard-summary/")
        self.assertEqual(res.status_code, 200, res.data)
        # svf_elzane (one of Elzane's two) + down_only.
        self.assertEqual(res.data["services_offline"], 2)
        self.assertEqual(res.data["services_offline_customers"], 2)

