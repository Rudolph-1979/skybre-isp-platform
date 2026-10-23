"""The preview has to project what the run will actually bill.

The defect these pin: `preview_export._project_invoice` built its lines
from the tariff price and knew nothing about discounts, while
`recurring._generate_document` added a negative discount line. A
customer on a 100% discount was therefore projected at R1,148.85 and
invoiced at R0.00 -- and the CSV bundle finance reviews, the grand total
on the screen, and the fingerprint the approval is taken over were all
built from the wrong one.

The fingerprint part is the sharpest edge. It is computed over the
projection's lines, so a discount added, changed or removed between
approval and Run did not move the digest, and Run's "something changed
since this was approved" refusal -- the entire reason the fingerprint
exists -- let it through.

Both sides now read billing/invoice_lines.py, so the tests that matter
compare the projection against the invoice the engine really writes,
rather than asserting a number either side could be wrong about on its
own.
"""
import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from billing.models import (
    CustomerBillingConfig, Invoice, InvoiceItem, Service, Tariff,
)
from billing.preview_export import build_csvs, project_run, projection_digest
from billing.recurring import run_recurring_billing
from customers.models import Customer
from billing.gate_testing import ApprovalGateOpenMixin
from notifications.models import EmailTemplate


class DiscountProjectionTests(ApprovalGateOpenMixin, TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        EmailTemplate.objects.get_or_create(
            key=EmailTemplate.Key.INVOICE,
            defaults={"subject": "Invoice", "body_html": "<p>x</p>"},
        )
        self.tariff = Tariff.objects.create(
            name="MTD 250 Mbps", price=Decimal("999.00"), tax_rate_pct=Decimal("15"),
            speed_download_kbps=256000, speed_upload_kbps=256000,
        )
        self.customer = Customer.objects.create(
            full_name="Rudolph Grunder", customer_id="CUS-001597",
            email="rudolph@example.com", status=Customer.Status.ACTIVE,
        )
        config = CustomerBillingConfig.objects.get_or_create(customer=self.customer)[0]
        config.billing_enabled = True
        config.auto_create_invoices = True
        config.send_billing_notifications = False
        config.billing_day = self.today.day
        config.next_billing_date = self.today
        config.save()
        self.service = Service.objects.create(
            customer=self.customer, tariff=self.tariff,
            status=Service.Status.ACTIVE, start_date="2026-01-01",
        )

    def _discount(self, kind, value, starts=None, ends=None):
        self.service.discount_type = kind
        self.service.discount_value = Decimal(value)
        self.service.discount_starts_on = starts
        self.service.discount_ends_on = ends
        self.service.discount_reason = "Staff account"
        self.service.save()

    def _projected_total(self):
        return project_run(self.today)["grand_total"]

    def _billed_total(self):
        run_recurring_billing(self.today, commit=True)
        return Invoice.objects.get(customer=self.customer).total

    # --- the projection matches the invoice -------------------------------

    def test_a_full_discount_projects_zero_and_bills_zero(self):
        """The reported case. Projected R1,148.85, billed R0.00 -- finance
        approved a number that was never going to be raised."""
        self._discount(Service.DiscountType.PERCENTAGE, "100")

        projected = self._projected_total()

        self.assertEqual(projected, Decimal("0.00"))
        self.assertEqual(self._billed_total(), projected)

    def test_a_partial_discount_projects_what_it_bills(self):
        self._discount(Service.DiscountType.PERCENTAGE, "25")

        projected = self._projected_total()

        # 999 - 25% = 749.25, +15% VAT = 861.64.
        self.assertEqual(projected, Decimal("861.64"))
        self.assertEqual(self._billed_total(), projected)

    def test_a_rand_discount_projects_what_it_bills(self):
        self._discount(Service.DiscountType.AMOUNT, "150.00")

        projected = self._projected_total()

        self.assertEqual(self._billed_total(), projected)

    def test_an_undiscounted_customer_is_unchanged(self):
        """The regression guard: nothing about the ordinary case moved."""
        projected = self._projected_total()

        self.assertEqual(projected, Decimal("1148.85"))
        self.assertEqual(self._billed_total(), projected)

    def test_the_projection_and_the_invoice_agree_where_rounding_splits_them(self):
        """R999 at 15% less 50% lands on a half-cent: 499.50 + 74.925.

        The invoice rounds subtotal and VAT once each and adds them
        (574.43). Rounding each LINE and adding gives 574.42. One cent, on
        every such customer, between what finance approved and what was
        billed -- and the sort of difference that is only ever found by
        someone reconciling a bank statement by hand.
        """
        self._discount(Service.DiscountType.PERCENTAGE, "50")

        projected = self._projected_total()

        self.assertEqual(projected, Decimal("574.43"))
        self.assertEqual(self._billed_total(), projected)

    def test_vat_is_charged_on_the_discounted_value(self):
        """A discount reduces the taxable supply, so the discount line
        carries the tariff's own rate and contributes negative VAT. Give it
        a zero rate and every discounted invoice over-declares output VAT."""
        self._discount(Service.DiscountType.PERCENTAGE, "50")

        self._billed_total()
        invoice = Invoice.objects.get(customer=self.customer)

        self.assertEqual(invoice.subtotal, Decimal("499.50"))
        self.assertEqual(invoice.tax_total, Decimal("74.93"))

    # --- the discount is visible, not folded away -------------------------

    def test_the_discount_is_its_own_line_on_the_invoice(self):
        self._discount(Service.DiscountType.PERCENTAGE, "25")
        self._billed_total()

        items = list(
            InvoiceItem.objects.filter(invoice__customer=self.customer).order_by("pk")
        )

        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].unit_price, Decimal("999.00"))
        self.assertEqual(items[0].item_type, InvoiceItem.ItemType.TARIFF)
        self.assertEqual(items[1].unit_price, Decimal("-249.75"))
        self.assertIn("Staff account", items[1].description)
        # Not a plan being sold -- hanging the tariff off it would make it
        # count as one anywhere that groups by tariff.
        self.assertIsNone(items[1].tariff)

    def test_the_discount_reaches_the_csv_finance_reviews(self):
        self._discount(Service.DiscountType.PERCENTAGE, "25")

        sheets = build_csvs(project_run(self.today))

        self.assertIn("Staff account", sheets["transaction"])
        self.assertIn("-249.75", sheets["transaction"])

    # --- the approval guard can now see discounts -------------------------

    def test_adding_a_discount_changes_the_fingerprint(self):
        """So an approval taken before the discount no longer authorises the
        run. This was the hole: the digest is built from the projection's
        lines, and the projection had no discount line to build from."""
        before = projection_digest(project_run(self.today))

        self._discount(Service.DiscountType.PERCENTAGE, "100")

        self.assertNotEqual(before, projection_digest(project_run(self.today)))

    def test_removing_a_discount_changes_the_fingerprint(self):
        self._discount(Service.DiscountType.PERCENTAGE, "50")
        before = projection_digest(project_run(self.today))

        self._discount(Service.DiscountType.NONE, "0")

        self.assertNotEqual(before, projection_digest(project_run(self.today)))

    # --- dated discounts --------------------------------------------------

    def test_a_discount_that_has_not_started_is_not_projected(self):
        self._discount(
            Service.DiscountType.PERCENTAGE, "100",
            starts=self.today + datetime.timedelta(days=1),
        )

        projected = self._projected_total()

        self.assertEqual(projected, Decimal("1148.85"))
        self.assertEqual(self._billed_total(), projected)

    def test_a_discount_that_has_expired_is_not_projected(self):
        self._discount(
            Service.DiscountType.PERCENTAGE, "100",
            ends=self.today - datetime.timedelta(days=1),
        )

        projected = self._projected_total()

        self.assertEqual(projected, Decimal("1148.85"))
        self.assertEqual(self._billed_total(), projected)

    def test_a_discount_ending_today_still_applies_today(self):
        """"Applies ON this date and stops the day after" -- the field's own
        promise, and the boundary somebody will set by hand."""
        self._discount(Service.DiscountType.PERCENTAGE, "100", ends=self.today)

        self.assertEqual(self._projected_total(), Decimal("0.00"))


class PreviewApiDiscountTests(TestCase):
    """The same thing through the endpoint finance actually uses."""

    def setUp(self):
        self.today = timezone.localdate()
        User = get_user_model()
        self.client = APIClient()
        self.client.force_authenticate(
            User.objects.create_user(
                username="clerk", password="x", role="admin", is_staff=True, email="c@example.com",
            )
        )
        EmailTemplate.objects.get_or_create(
            key=EmailTemplate.Key.INVOICE,
            defaults={"subject": "Invoice", "body_html": "<p>x</p>"},
        )
        tariff = Tariff.objects.create(
            name="MTD 250 Mbps", price=Decimal("999.00"), tax_rate_pct=Decimal("15"),
            speed_download_kbps=256000, speed_upload_kbps=256000,
        )
        customer = Customer.objects.create(
            full_name="Rudolph Grunder", customer_id="CUS-001597",
            email="rudolph@example.com", status=Customer.Status.ACTIVE,
        )
        config = CustomerBillingConfig.objects.get_or_create(customer=customer)[0]
        config.billing_enabled = True
        config.auto_create_invoices = True
        config.send_billing_notifications = False
        config.billing_day = self.today.day
        config.next_billing_date = self.today
        config.save()
        Service.objects.create(
            customer=customer, tariff=tariff, status=Service.Status.ACTIVE,
            start_date="2026-01-01", discount_type=Service.DiscountType.PERCENTAGE,
            discount_value=Decimal("100"), discount_reason="Staff account",
        )

    def test_the_preview_screen_shows_the_discounted_total(self):
        response = self.client.post(
            "/api/recurring-billing/preview/", {"date": self.today.isoformat()}, format="json"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["snapshot"]["grand_total"], "0.00")
        # Still a document -- a fully discounted customer gets an invoice
        # for R0.00, not silence. It is the record that the discount was
        # applied, and the thing they can be shown when they ask.
        self.assertEqual(response.data["counts"]["invoices_created"], 1)
        self.assertNotIn("mismatch", response.data)
