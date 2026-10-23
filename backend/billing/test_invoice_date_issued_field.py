"""Invoice.date_issued, exposed read-only through the API.

Rudolph asked for the customer page's separate Invoices and Payments
tables to become one combined "Statement" list, sorted by date. The
frontend already had every OTHER field it needed (number, total,
paid_amount, status, date_created); the one thing missing was the date a
document actually counts from -- date_due is a payment-terms date, not
an issue date, and billing.statements.movements() (the same ordering the
"Preview statement" PDF uses) sorts by date_issued (falling back to
date_created), not date_due. So the frontend needed date_issued too, to
sort the merged list the same way the PDF already does.

Exposed read-only, not writable: Invoice.save() stamps this itself the
moment a document first becomes a real tax invoice (see that method's own
comment) -- it was never meant to be set by hand through the API, and
adding it to `fields` without adding it to `read_only_fields` would have
made it one.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.testing import ALL_SECTIONS
from billing.models import Invoice
from customers.models import Customer

User = get_user_model()


class InvoiceDateIssuedFieldTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.staff = User.objects.create_user(
            username="finance-di", password="pw-for-tests", role=User.Role.ACCOUNTS,
            allowed_sections=ALL_SECTIONS,
        )
        self.client.force_authenticate(self.staff)
        self.customer = Customer.objects.create(
            full_name="Issued Ivy", email="ivy@example.com", balance=Decimal("0.00"),
        )

    def _create(self, status="unpaid", extra=None):
        payload = {
            "customer": self.customer.id, "status": status, "date_due": "2026-09-30",
            "items": [{
                "description": "Monthly service", "quantity": 1,
                "unit_price": "500.00", "tax_rate_pct": "0.00",
            }],
        }
        if extra:
            payload.update(extra)
        res = self.client.post("/api/invoices/", payload, format="json")
        self.assertEqual(res.status_code, 201, res.data)
        return res.data["id"]

    def test_a_real_invoice_reports_its_date_issued(self):
        # InvoiceCreateSerializer (the create action's own serializer,
        # deliberately narrow -- see its class docstring) doesn't carry
        # date_issued at all; InvoiceSerializer (list/retrieve) is the one
        # the frontend's Statement view actually reads it from.
        invoice_id = self._create()
        res = self.client.get(f"/api/invoices/{invoice_id}/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["date_issued"], timezone.localdate().isoformat())

    def test_a_quote_has_no_date_issued_yet(self):
        invoice_id = self._create(status="quote")
        res = self.client.get(f"/api/invoices/{invoice_id}/")
        self.assertEqual(res.status_code, 200)
        self.assertIsNone(res.data["date_issued"])

    def test_date_issued_cannot_be_set_by_hand(self):
        """Read-only: Invoice.save() decides this itself. A caller trying
        to backdate it through the API is silently ignored, the same way
        every other read-only field on this serializer already is."""
        invoice_id = self._create(extra={"date_issued": "2020-01-01"})
        res = self.client.get(f"/api/invoices/{invoice_id}/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["date_issued"], timezone.localdate().isoformat())

    def test_it_matches_the_stored_model_field(self):
        invoice = Invoice.objects.create(
            customer=self.customer, status=Invoice.Status.UNPAID, date_due=timezone.localdate(),
        )
        res = self.client.get(f"/api/invoices/{invoice.pk}/")
        self.assertEqual(res.status_code, 200)
        invoice.refresh_from_db()
        self.assertEqual(res.data["date_issued"], invoice.date_issued.isoformat())
