"""The "Online" client status (Rudolph, 6 Oct 2026) behaves like Active."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from billing.models import Service, Tariff
from customers.models import Customer

User = get_user_model()


class OnlineStatusTests(TestCase):
    def setUp(self):
        self.api = APIClient()
        self.api.force_authenticate(User.objects.create_user(username="a", password="x", role="admin"))

    def test_create_as_online_and_filter(self):
        res = self.api.post("/api/customers/", {"full_name": "Live Lisa", "status": "online"}, format="json")
        self.assertEqual(res.status_code, 201, res.data)
        names = [r["full_name"] for r in self.api.get("/api/customers/?status_any=online").data["results"]]
        self.assertEqual(names, ["Live Lisa"])

    def test_online_restores_lines_like_active(self):
        tariff = Tariff.objects.create(name="T", price=Decimal("1"), speed_download_kbps=1, speed_upload_kbps=1)
        c = Customer.objects.create(full_name="Back On", status="active")
        s = Service.objects.create(customer=c, tariff=tariff, status="active", start_date="2026-08-01")
        c.status = "suspended"
        c.save()
        s.refresh_from_db()
        self.assertEqual(s.status, "suspended")
        c.status = "online"
        c.save()
        s.refresh_from_db()
        self.assertEqual(s.status, "active")

    def test_counts_as_active_on_the_dashboard(self):
        Customer.objects.create(full_name="A", status="active")
        Customer.objects.create(full_name="O", status="online")
        Customer.objects.create(full_name="N", status="new")
        from customers.models import Customer as C
        self.assertEqual(C.objects.filter(status__in=C.LIVE_STATUSES).count(), 2)
