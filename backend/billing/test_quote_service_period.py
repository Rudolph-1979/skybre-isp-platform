"""Accepting a quote must not schedule the customer's disconnection.

A tariff line on a quote carries From and Till, both `required` in the
invoice editor and both enforced by InvoiceItemSerializer.validate. They
describe the period the line is CHARGING for -- "September", "the first
12 months" -- and staff have no way to leave Till blank.

Invoice.activate_tariff_services wrote that Till straight into
Service.end_date. Service.end_date is not a billing period: it is the
instruction billing.cancellations acts on, and its module docstring is
explicit that the end date is "the first day WITHOUT service". So
accepting a quote booked the customer's own disconnection, on the last
day of the period they had just paid for, plus a day's slip from the
inclusive/exclusive mismatch. When that date arrived the nightly job
terminated the service -- rewriting their RADIUS rows to reject and
dropping the live session -- and, because it was their only service,
marked the customer Inactive. Cancelled.

Every customer who came in on an accepted quote was carrying that. It
never announced itself at signup, only a month or a year later, as an
apparently spontaneous disconnection of a paying customer.

The fix is to stop reading a mandatory billing period as a termination
instruction. Service.end_date stays what it has always been -- something
staff set deliberately, on the service, when a customer is actually
leaving -- and the quote's period stays on the invoice line, which is
where it is a true statement about what was charged.
"""
import datetime
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from billing.cancellations import apply_due_cancellations
from billing.models import Invoice, InvoiceItem, Service, Tariff
from customers.models import Customer


class QuoteToServiceTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.tariff = Tariff.objects.create(
            name="Home 20", price=Decimal("500.00"), tax_rate_pct=Decimal("15.00"),
            speed_download_kbps=20480, speed_upload_kbps=10240,
        )
        self.customer = Customer.objects.create(
            full_name="Anna Able", email="anna@example.com", status=Customer.Status.ACTIVE,
        )

    def _accepted_quote(self, period_start, period_end):
        """A quote with one tariff line, accepted and converted."""
        quote = Invoice.objects.create(
            customer=self.customer, status=Invoice.Status.QUOTE,
            date_due=period_start + datetime.timedelta(days=7),
        )
        InvoiceItem.objects.create(
            invoice=quote, item_type=InvoiceItem.ItemType.TARIFF, tariff=self.tariff,
            description="Home 20 (Monthly)", quantity=1, unit_price=self.tariff.price,
            tax_rate_pct=self.tariff.tax_rate_pct,
            period_start=period_start, period_end=period_end,
        )
        quote.recalc_totals()
        quote.convert_to_invoice()
        return quote

    def test_the_service_gets_no_end_date_from_the_quoted_period(self):
        month_start = self.today.replace(day=1)
        month_end = month_start + datetime.timedelta(days=29)

        self._accepted_quote(month_start, month_end)

        service = Service.objects.get(customer=self.customer)
        self.assertIsNone(
            service.end_date,
            "accepting a quote booked the customer's disconnection",
        )

    def test_the_service_still_records_when_it_started(self):
        """Only the termination half is wrong. The start date is a fact."""
        month_start = self.today.replace(day=1)

        self._accepted_quote(month_start, month_start + datetime.timedelta(days=29))

        service = Service.objects.get(customer=self.customer)
        self.assertEqual(service.start_date, month_start)

    def test_the_invoice_line_keeps_the_period_it_charged_for(self):
        """The period is a true statement about the charge -- it belongs on
        the line, and the tax invoice prints it. Nothing here removes it."""
        month_start = self.today.replace(day=1)
        month_end = month_start + datetime.timedelta(days=29)

        quote = self._accepted_quote(month_start, month_end)

        item = quote.items.get()
        self.assertEqual(item.period_start, month_start)
        self.assertEqual(item.period_end, month_end)

    def test_the_nightly_cancellation_job_leaves_the_new_customer_alone(self):
        """The end-to-end version: the defect only ever showed up here."""
        period_end = self.today - datetime.timedelta(days=1)
        self._accepted_quote(period_end - datetime.timedelta(days=29), period_end)

        ended, cancelled = apply_due_cancellations(as_of=self.today, commit=True)

        service = Service.objects.get(customer=self.customer)
        self.customer.refresh_from_db()
        self.assertEqual(ended, [])
        self.assertEqual(cancelled, [])
        self.assertEqual(service.status, Service.Status.ACTIVE)
        self.assertEqual(
            self.customer.status, Customer.Status.ACTIVE,
            "a paying customer was cancelled for having accepted a quote",
        )

    def test_staff_can_still_end_a_service_deliberately(self):
        """The deliberate path has to keep working -- it is the one that
        should have been carrying this all along."""
        self._accepted_quote(
            self.today - datetime.timedelta(days=30), self.today - datetime.timedelta(days=1)
        )
        service = Service.objects.get(customer=self.customer)
        service.end_date = self.today
        service.save(update_fields=["end_date"])

        ended, cancelled = apply_due_cancellations(as_of=self.today, commit=True)

        service.refresh_from_db()
        self.customer.refresh_from_db()
        self.assertEqual(len(ended), 1)
        self.assertEqual(len(cancelled), 1)
        self.assertEqual(service.status, Service.Status.TERMINATED)
        self.assertEqual(self.customer.status, Customer.Status.INACTIVE)
