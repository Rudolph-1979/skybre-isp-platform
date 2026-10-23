"""Customers list: Last seen sortable (#200, Rudolph 19 Oct 2026: "Make Last
Seen Sortable As well please"). Ascending = longest ago first, a line never
seen counts as oldest, customers with no line of ours always last."""
import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from billing.models import Service, Tariff
from customers.models import Customer
from radiusauth.models import RadAcct

User = get_user_model()


class LastSeenSortTests(TestCase):
    def setUp(self):
        now = timezone.now()
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_user(username="boss", password="x", role="admin"))
        tariff = Tariff.objects.create(name="Vuma", price=100, speed_download_kbps=1024, speed_upload_kbps=1024)

        def customer(name, login=None):
            c = Customer.objects.create(full_name=name, status=Customer.Status.ACTIVE)
            if login:
                Service.objects.create(customer=c, tariff=tariff, status=Service.Status.ACTIVE,
                                       start_date="2026-10-05", radius_username=login)
            return c

        def seen(login, minutes_ago):
            t = now - datetime.timedelta(minutes=minutes_ago)
            RadAcct.objects.create(acctsessionid=login, acctuniqueid=login, username=login, nasipaddress="10.0.0.1",
                                   acctstarttime=t - datetime.timedelta(hours=1), acctupdatetime=t)

        customer("Now Online", "a"); seen("a", 1)
        customer("Down a day", "b"); seen("b", 1440)
        customer("Down an hour", "c"); seen("c", 60)
        customer("Never Seen", "d")
        customer("Elsewhere")

    def names(self, ordering, page_size=100, page=1):
        res = self.client.get(f"/api/customers/?ordering={ordering}&page_size={page_size}&page={page}")
        self.assertEqual(res.status_code, 200, res.data)
        return [r["full_name"] for r in res.data["results"]], res.data

    def test_oldest_first(self):
        names, _ = self.names("last_seen")
        self.assertEqual(names, ["Never Seen", "Down a day", "Down an hour", "Now Online", "Elsewhere"])

    def test_newest_first_no_line_still_last(self):
        names, _ = self.names("-last_seen")
        self.assertEqual(names, ["Now Online", "Down an hour", "Down a day", "Never Seen", "Elsewhere"])

    def test_paged_and_columns_filled(self):
        names, data = self.names("-last_seen", page_size=2, page=2)
        self.assertEqual(names, ["Down a day", "Never Seen"])
        self.assertEqual(data["count"], 5)
        self.assertIsNotNone(data["results"][0]["last_seen_at"])
        self.assertIsNone(data["results"][1]["last_seen_at"])
        self.assertFalse(data["results"][0]["online"])
