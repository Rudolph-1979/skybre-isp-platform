"""Editable service start date (Rudolph, 7 Oct 2026: "service is created but
only goes live two days later ... I need to edit the service start date for
billing purposes")."""
import datetime
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from billing.invoice_lines import billable_services
from billing.models import Service, Tariff
from customers.models import Customer


class ServiceStartDateTests(TestCase):
    def setUp(self):
        admin = User.objects.create_user(username="boss", password="x", role="admin")
        self.api = APIClient()
        self.api.force_authenticate(admin)
        self.customer = Customer.objects.create(full_name="Jan", customer_id="SWF3002", address="1 Main", city="X")
        tariff = Tariff.objects.create(name="10 Mbps", price=Decimal("400"))
        self.service = Service.objects.create(customer=self.customer, tariff=tariff, status="active",
                                              start_date=datetime.date(2026, 10, 7))

    def test_edit_start_date(self):
        res = self.api.patch(f"/api/services/{self.service.pk}/", {"start_date": "2026-10-09"}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.service.refresh_from_db()
        self.assertEqual(self.service.start_date, datetime.date(2026, 10, 9))

    def test_end_before_start_refused(self):
        res = self.api.patch(f"/api/services/{self.service.pk}/",
                             {"start_date": "2026-10-09", "end_date": "2026-10-08"}, format="json")
        self.assertEqual(res.status_code, 400)

    def test_not_billed_before_it_starts(self):
        Service.objects.filter(pk=self.service.pk).update(start_date=datetime.date(2026, 10, 9))
        self.assertEqual(billable_services(self.customer, datetime.date(2026, 10, 8)), [])
        self.assertEqual(len(billable_services(self.customer, datetime.date(2026, 10, 9))), 1)
        self.assertEqual(len(billable_services(self.customer, datetime.date(2026, 11, 1))), 1)
