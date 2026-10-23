"""The list of once-off invoice lines (Rudolph, 6 Oct 2026: "Once off invoice
line items should be creatable")."""
import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from customers.models import Customer

from .models import Invoice, OnceOffItem

User = get_user_model()


class OnceOffItemTests(TestCase):
    def setUp(self):
        self.api = APIClient()
        self.api.force_authenticate(User.objects.create_user(username="boss", password="x", role="admin"))
        self.customer = Customer.objects.create(full_name="Jan Botha")

    def test_create_list_and_edit(self):
        res = self.api.post("/api/once-off-items/", {"name": " Installation ", "price": "1500.00"}, format="json")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual((res.data["name"], res.data["tax_rate_pct"]), ("Installation", "15.00"))
        dup = self.api.post("/api/once-off-items/", {"name": "installation", "price": "1"}, format="json")
        self.assertEqual(dup.status_code, 400)
        self.assertEqual(self.api.post("/api/once-off-items/", {"name": "X", "price": "-1"}, format="json").status_code, 400)
        patch = self.api.patch(f"/api/once-off-items/{res.data['id']}/", {"price": "1650.00"}, format="json")
        self.assertEqual(patch.data["price"], "1650.00")
        names = [r["name"] for r in self.api.get("/api/once-off-items/?is_active=true").data["results"]]
        self.assertEqual(names, ["Installation"])

    def test_used_on_an_invoice_and_not_deletable_then(self):
        item = OnceOffItem.objects.create(name="6 m pole", price=Decimal("450.00"))
        res = self.api.post("/api/invoices/", {
            "customer": self.customer.id, "status": "quote", "date_due": str(datetime.date.today()),
            "items": [{"item_type": "once_off_item", "once_off_item": item.id, "description": "6 m pole",
                       "quantity": 1, "unit_price": "450.00", "tax_rate_pct": "15.00"}],
        }, format="json")
        self.assertEqual(res.status_code, 201, res.data)
        line = Invoice.objects.get(pk=res.data["id"]).items.get()
        self.assertEqual(line.once_off_item, item)
        detail = self.api.get(f"/api/invoices/{res.data['id']}/").data["items"][0]
        self.assertEqual(detail["once_off_item_name"], "6 m pole")
        self.assertEqual(self.api.delete(f"/api/once-off-items/{item.id}/").status_code, 400)
        unused = OnceOffItem.objects.create(name="Call-out", price=Decimal("350.00"))
        self.assertEqual(self.api.delete(f"/api/once-off-items/{unused.id}/").status_code, 204)

    def test_a_once_off_line_must_name_its_item(self):
        res = self.api.post("/api/invoices/", {
            "customer": self.customer.id, "status": "quote", "date_due": str(datetime.date.today()),
            "items": [{"item_type": "once_off_item", "description": "x", "quantity": 1, "unit_price": "1"}],
        }, format="json")
        self.assertEqual(res.status_code, 400)

    def test_only_configs_can_change_the_list(self):
        clerk = User.objects.create_user(username="clerk", password="x", role="support", allowed_sections=["finance"])
        api = APIClient()
        api.force_authenticate(clerk)
        self.assertEqual(api.get("/api/once-off-items/").status_code, 200)
        self.assertEqual(api.post("/api/once-off-items/", {"name": "X", "price": "1"}, format="json").status_code, 403)
