"""Comments for the client on quotes/invoices (George, 14 Oct 2026)."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from billing.models import Invoice
from customers.models import Customer


class QuoteCommentTests(TestCase):
    def setUp(self):
        self.api = APIClient()
        self.api.force_authenticate(get_user_model().objects.create_user(username="adm", password="x", role="admin"))
        self.customer = Customer.objects.create(full_name="Janine Erasmus", status="active")

    def test_comment_saved_on_create_editable_and_on_the_pdf(self):
        res = self.api.post("/api/invoices/", {
            "customer": self.customer.pk, "date_due": "2026-10-21", "status": "quote",
            "note": "Installation within 5 working days.\nPrice valid for 7 days.",
            "items": [{"item_type": "custom", "description": "Call-out", "quantity": 1,
                       "unit_price": "217.39", "tax_rate_pct": "15"}],
        }, format="json")
        self.assertEqual(res.status_code, 201, res.data)
        inv = Invoice.objects.get(pk=res.data["id"])
        self.assertIn("5 working days", inv.note)
        res = self.api.patch(f"/api/invoices/{inv.pk}/", {"note": "Updated: valid for 14 days."}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        from notifications.pdf import render_invoice_pdf
        from django.template.loader import render_to_string  # noqa: F401

        inv.refresh_from_db()
        self.assertEqual(inv.note, "Updated: valid for 14 days.")
        self.assertTrue(render_invoice_pdf(inv, "Skybre").startswith(b"%PDF"))

    def test_template_prints_the_comment(self):
        inv = Invoice.objects.create(customer=self.customer, status="quote", date_due="2026-10-21",
                                     note="Line one\nLine two")
        from django.template.loader import render_to_string

        html = render_to_string("notifications/invoice_pdf.html", {
            "invoice": inv, "company": {}, "rows": [], "customer": self.customer,
            "total_exclusive": Decimal("0"), "total_tax": Decimal("0"), "balance_due": Decimal("0"),
        })
        self.assertIn("Comments", html)
        self.assertIn("Line one<br>Line two", html)
        self.assertNotIn("Invoice.note", html)  # the template's own comment must not print
