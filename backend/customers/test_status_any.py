"""Customers status filter: one or more at once (Rudolph, 6 Oct 2026)."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from billing.models import Service, Tariff
from customers.models import Customer

User = get_user_model()


class StatusAnyTests(TestCase):
    def setUp(self):
        self.api = APIClient()
        self.api.force_authenticate(User.objects.create_user(username="a", password="x", role="admin"))
        tariff = Tariff.objects.create(name="T", price=Decimal("1"), speed_download_kbps=1, speed_upload_kbps=1)
        Customer.objects.create(full_name="New Nina", status="new")
        Customer.objects.create(full_name="Bad Ben", status="bad_debt")
        Customer.objects.create(full_name="Gone Gert", status="inactive")
        unpaid = Customer.objects.create(full_name="Unpaid Una", status="active")
        manual = Customer.objects.create(full_name="Manual Manie", status="active")
        s1 = Service.objects.create(customer=unpaid, tariff=tariff, status="active", start_date="2026-08-01")
        s2 = Service.objects.create(customer=manual, tariff=tariff, status="active", start_date="2026-08-01")
        Service.objects.filter(pk=s1.pk).update(status="suspended", suspended_for_nonpayment=True)
        Service.objects.filter(pk=s2.pk).update(status="suspended", suspended_for_nonpayment=False)

    def names(self, value):
        res = self.api.get(f"/api/customers/?page_size=100&status_any={value}")
        self.assertEqual(res.status_code, 200)
        return sorted(r["full_name"] for r in res.data["results"])

    def test_several_statuses(self):
        self.assertEqual(self.names("bad_debt,inactive"), ["Bad Ben", "Gone Gert"])

    def test_status_plus_line_suspended(self):
        self.assertEqual(self.names("new,line_suspended_nonpayment"), ["New Nina", "Unpaid Una"])
        self.assertEqual(self.names("line_suspended"), ["Manual Manie", "Unpaid Una"])

    def test_empty_means_all(self):
        self.assertEqual(len(self.names("")), 5)
