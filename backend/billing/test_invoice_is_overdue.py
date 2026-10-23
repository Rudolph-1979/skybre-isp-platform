"""Invoice.is_overdue -- the fix for "TXN92206 still just says Unpaid.
It should have changed to Partially Paid - Overdue."

Package #54 shipped InvoiceStatusBadge (the frontend's Unpaid / Paid /
Partially Paid / Overdue / Partially Paid - Overdue categorisation) and
built it against `invoice.status === "overdue"` -- which reads as a
reasonable thing to check, right up until you notice that value is never
actually assigned to a real invoice anywhere in this codebase (see
billing.overdue's module docstring and customers.filters.CustomerFilter.
filter_overdue, which both already document this, and seed_demo_data.py,
the one place that DOES set it, which is demo-data-only). An invoice's
`status` field sits at UNPAID forever, however long ago date_due passed,
unless something hand-edits it -- so the new "Overdue" categorisation
could never actually appear for any genuine invoice, which is exactly what
Rudolph saw happen to TXN92206.

The fix: Invoice.is_overdue, a read-only computed property (mirroring the
existing CustomerTask.is_overdue) using the platform's one existing
definition of "overdue" (billing.overdue.overdue_q: still owed, i.e.
unpaid or literal OVERDUE, AND date_due in the past) -- computed server-
side, not re-derived from the status string in the browser. Exposed
read-only through InvoiceSerializer so the frontend badge can just read
it rather than reimplementing (and getting wrong) its own notion of
overdue.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.testing import ALL_SECTIONS
from billing.models import Invoice
from customers.models import Customer

User = get_user_model()

TODAY = timezone.localdate()
YESTERDAY = TODAY - timedelta(days=1)
TOMORROW = TODAY + timedelta(days=1)


class InvoiceIsOverdueModelTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(
            full_name="Overdue Olive", email="olive@example.com", balance=Decimal("0.00"),
        )

    def _invoice(self, status, date_due, paid_amount="0.00", total="500.00"):
        inv = Invoice.objects.create(
            customer=self.customer, status=status, date_due=date_due,
        )
        # total/paid_amount aren't constructor kwargs worth depending on here
        # (recalc_totals() derives total from line items) -- set directly,
        # the same way the credit-note-invoice code already does.
        Invoice.objects.filter(pk=inv.pk).update(total=Decimal(total), paid_amount=Decimal(paid_amount))
        inv.refresh_from_db()
        return inv

    def test_unpaid_and_past_due_is_overdue(self):
        inv = self._invoice(Invoice.Status.UNPAID, YESTERDAY)
        self.assertTrue(inv.is_overdue)

    def test_unpaid_but_not_yet_due_is_not_overdue(self):
        inv = self._invoice(Invoice.Status.UNPAID, TOMORROW)
        self.assertFalse(inv.is_overdue)

    def test_unpaid_due_today_is_not_overdue_yet(self):
        # overdue_q() is a strict `date_due__lt=as_of` -- due today hasn't
        # passed yet, it's due today.
        inv = self._invoice(Invoice.Status.UNPAID, TODAY)
        self.assertFalse(inv.is_overdue)

    def test_partially_paid_and_past_due_is_overdue(self):
        # The exact TXN92206 shape: unpaid status, something paid, nothing
        # like the full total, due date well in the past.
        inv = self._invoice(Invoice.Status.UNPAID, YESTERDAY, paid_amount="150.00", total="500.00")
        self.assertTrue(inv.is_overdue)

    def test_paid_invoice_is_never_overdue_however_late(self):
        inv = self._invoice(Invoice.Status.PAID, YESTERDAY, paid_amount="500.00", total="500.00")
        self.assertFalse(inv.is_overdue)

    def test_cancelled_invoice_is_never_overdue(self):
        inv = self._invoice(Invoice.Status.CANCELLED, YESTERDAY)
        self.assertFalse(inv.is_overdue)

    def test_quote_is_never_overdue(self):
        inv = self._invoice(Invoice.Status.QUOTE, YESTERDAY)
        self.assertFalse(inv.is_overdue)

    def test_the_rarely_used_literal_overdue_status_is_still_overdue(self):
        # Status.OVERDUE is never actually assigned in production code, but
        # nothing stops a script (or a future feature) from setting it, and
        # billing.overdue's own OWED_STATUSES counts it -- this property
        # must agree.
        inv = self._invoice(Invoice.Status.OVERDUE, YESTERDAY)
        self.assertTrue(inv.is_overdue)

    # date_due is a required (NOT NULL) field on every real invoice -- there
    # is no "no due date" state to construct through the ORM. is_overdue's
    # own `if not self.date_due` guard is defensive (a QUOTE, for instance,
    # is created with a placeholder due date before it ever matters) rather
    # than something reachable here, so it isn't separately exercised.


class InvoiceIsOverdueApiTests(TestCase):
    """The frontend badge reads this through the API, not the model
    directly -- confirm InvoiceSerializer actually exposes it, read-only."""

    def setUp(self):
        self.client = APIClient()
        self.staff = User.objects.create_user(
            username="finance-io", password="pw-for-tests", role=User.Role.ACCOUNTS,
            allowed_sections=ALL_SECTIONS,
        )
        self.client.force_authenticate(self.staff)
        self.customer = Customer.objects.create(
            full_name="Overdue Api Ivy", email="ivy-io@example.com", balance=Decimal("0.00"),
        )

    def test_is_overdue_is_on_the_invoice_payload(self):
        inv = Invoice.objects.create(customer=self.customer, status=Invoice.Status.UNPAID, date_due=YESTERDAY)
        Invoice.objects.filter(pk=inv.pk).update(total=Decimal("500.00"), paid_amount=Decimal("150.00"))
        res = self.client.get(f"/api/invoices/{inv.pk}/")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["is_overdue"])

    def test_cannot_be_set_by_hand(self):
        """Read-only, same as date_issued and can_convert_to_*: this is
        computed from status/date_due, never accepted from the client.

        InvoiceCreateSerializer (the create action's own serializer,
        deliberately narrow) doesn't carry is_overdue at all -- confirm via
        the detail view, the same way test_invoice_date_issued_field.py's
        equivalent test does for date_issued."""
        res = self.client.post("/api/invoices/", {
            "customer": self.customer.id, "status": "unpaid", "date_due": "2020-01-01",
            "is_overdue": False,
            "items": [{
                "description": "Monthly service", "quantity": 1,
                "unit_price": "500.00", "tax_rate_pct": "0.00",
            }],
        }, format="json")
        self.assertEqual(res.status_code, 201, res.data)
        detail = self.client.get(f"/api/invoices/{res.data['id']}/")
        # Whatever the client sent for is_overdue is ignored -- it's
        # genuinely overdue (due 2020-01-01), so it reads True regardless.
        self.assertTrue(detail.data["is_overdue"])
