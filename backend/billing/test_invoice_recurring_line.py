"""A Recurring charge line on a hand-made invoice or quote (Rudolph, 7 Oct
2026: "Recurring needs to show" in the New invoice line types)."""
import datetime
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from billing.models import Invoice, RecurringCharge
from customers.models import Customer


class RecurringChargeLineTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="boss", password="x", role="admin")
        self.api = APIClient()
        self.api.force_authenticate(self.admin)
        self.customer = Customer.objects.create(full_name="Jan Botha", customer_id="CUS-000777")
        self.charge = RecurringCharge.objects.create(name="IP Rental", price=Decimal("100.00"),
                                                     tax_rate_pct=Decimal("15"))

    def post(self, line):
        return self.api.post("/api/invoices/", {
            "customer": self.customer.id, "status": "unpaid", "date_due": str(datetime.date.today()),
            "items": [{"description": "IP Rental", "quantity": 1, "unit_price": "100.00",
                       "tax_rate_pct": "15.00", **line}],
        }, format="json")

    def test_line_keeps_its_charge(self):
        res = self.post({"item_type": "recurring_charge", "recurring_charge": self.charge.id})
        self.assertEqual(res.status_code, 201, res.data)
        invoice = Invoice.objects.get(pk=res.data["id"])
        self.assertEqual(invoice.items.get().recurring_charge, self.charge)
        self.assertEqual(invoice.total, Decimal("115.00"))
        detail = self.api.get(f"/api/invoices/{invoice.id}/").data["items"][0]
        self.assertEqual(detail["recurring_charge_name"], "IP Rental")

    def test_must_name_the_charge(self):
        self.assertEqual(self.post({"item_type": "recurring_charge"}).status_code, 400)
