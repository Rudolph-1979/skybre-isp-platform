"""Walk a missed payment day by day and print what the customer receives.

Built to answer one question honestly: given an invoice due on the 12th
that nobody pays, what lands in the customer's inbox, on which day, and
what does it say?

It uses the REAL engine -- billing.recurring's own warning and blocking
passes, the real templates, the real context builder. Nothing here
reimplements the rules, because a demo that agreed with a second copy of
the logic would prove nothing about the first.

Two things are faked, both stated out loud in the output:

  * the mailer. send_customer_email is swapped for a stub that captures
    what would have been sent, so this can be run against production
    without emailing anybody.
  * the settings. An in-memory SuspensionSettings is passed in rather
    than the saved one, so running this never changes your platform-wide
    auto-suspension switch.

Everything else is real, including the database writes -- the demo
customer is created, billed, warned and suspended for real, then deleted
again unless --keep is given.

Usage:
    python manage.py demo_suspension_timeline
    python manage.py demo_suspension_timeline --due 2026-09-12 --blocking-days 3
    python manage.py demo_suspension_timeline --warning-days 3 --full
    python manage.py demo_suspension_timeline --keep
"""
import datetime
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.template import Context, Template
from django.utils import timezone

from billing import recurring
from billing.models import (
    CustomerBillingConfig, Invoice, InvoiceItem, Service, SuspensionSettings, Tariff,
)
from customers.models import Customer
from notifications.models import EmailTemplate

DEMO_REFERENCE = "DEMO-SUSPEND"


class _CapturedEmail:
    def __init__(self, template_key, customer, extra_context):
        self.template_key = template_key
        self.customer = customer
        self.extra_context = extra_context or {}
        self.status = "sent"   # so the engine's on_sent dedupe records it


class Command(BaseCommand):
    help = "Show, day by day, what a customer receives when they miss a payment."

    def add_arguments(self, parser):
        parser.add_argument("--due", default="", metavar="YYYY-MM-DD",
                            help="The invoice due date. Default: the 12th of this month.")
        parser.add_argument("--blocking-days", type=int, default=3,
                            help="Days after due before the line is cut off. Default 3.")
        parser.add_argument("--warning-days", type=int, default=3,
                            help="Notice period for the warning email. Default 3.")
        parser.add_argument("--amount", default="575.00", help="Invoice total. Default 575.00.")
        parser.add_argument("--full", action="store_true",
                            help="Print the whole email body, not just the subject and key lines.")
        parser.add_argument("--keep", action="store_true",
                            help="Leave the demo customer in the database afterwards.")

    def handle(self, *args, **options):
        if options["due"]:
            try:
                due = datetime.date.fromisoformat(options["due"])
            except ValueError:
                self.stderr.write(self.style.ERROR("--due must be YYYY-MM-DD."))
                return
        else:
            due = timezone.localdate().replace(day=12)

        blocking_days = options["blocking_days"]
        warning_days = options["warning_days"]
        cut_off = due + datetime.timedelta(days=blocking_days)

        if not EmailTemplate.objects.filter(key=EmailTemplate.Key.SUSPENSION_WARNING).exists():
            self.stderr.write(self.style.ERROR(
                "No 'suspension_warning' template exists, so there is nothing to show. "
                "Run migrate, or create it under Configs -> Email templates."
            ))
            return

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("SCENARIO"))
        self.stdout.write(f"  Invoice due            {due}")
        self.stdout.write(f"  Blocking period        {blocking_days} day(s) after due")
        self.stdout.write(f"  Cut-off date           {cut_off}")
        self.stdout.write(f"  Warning notice period  {warning_days} day(s)")
        self.stdout.write("  Nobody pays.")
        self.stdout.write("")

        captured = []
        real_sender = recurring.send_customer_email

        def _capture(template_key, customer, **kwargs):
            captured.append(_CapturedEmail(template_key, customer, kwargs.get("extra_context")))
            return captured[-1]

        customer = None
        try:
            customer = self._seed(due, blocking_days, options["amount"])
            recurring.send_customer_email = _capture

            # In-memory, never saved: this must not touch the real
            # platform-wide auto-suspension switch.
            settings = SuspensionSettings(
                auto_suspend_enabled=True, warning_enabled=True, warning_days=warning_days,
            )

            self.stdout.write(self.style.SUCCESS("TIMELINE"))
            day = due
            while day <= cut_off + datetime.timedelta(days=1):
                self._simulate_day(customer, settings, day, due, captured, options["full"])
                day += datetime.timedelta(days=1)

            self._summary(customer, captured, warning_days, blocking_days)
        finally:
            recurring.send_customer_email = real_sender
            if customer is not None and not options["keep"]:
                self._cleanup(customer)
                self.stdout.write("")
                self.stdout.write("Demo customer deleted. Use --keep to leave it in place.")
            elif customer is not None:
                self.stdout.write("")
                self.stdout.write(self.style.WARNING(
                    f"Demo customer {DEMO_REFERENCE} LEFT IN THE DATABASE (--keep)."
                ))

    # --- the scenario -----------------------------------------------------

    def _seed(self, due, blocking_days, amount):
        self._cleanup_existing()
        tariff, _ = Tariff.objects.get_or_create(
            name="DEMO Fibre 50/50",
            defaults={
                "price": Decimal(amount), "tax_rate_pct": Decimal("0"),
                "speed_download_kbps": 51200, "speed_upload_kbps": 51200,
            },
        )
        customer = Customer.objects.create(
            full_name="Demo Dumisani", customer_id=DEMO_REFERENCE,
            email="demo@example.invalid", status=Customer.Status.ACTIVE,
            balance=Decimal(amount), city="Nelspruit",
        )
        Service.objects.create(
            customer=customer, tariff=tariff, status=Service.Status.ACTIVE,
            start_date=due - datetime.timedelta(days=60),
        )
        config = CustomerBillingConfig.for_customer(customer)
        config.billing_enabled = True
        # Off: this demo is about what happens AFTER an invoice is missed,
        # and a new invoice landing mid-timeline would muddy it.
        config.auto_create_invoices = False
        config.send_billing_notifications = True
        config.blocking_period_days = blocking_days
        config.minimum_balance = Decimal("0")
        config.save()

        invoice = Invoice.objects.create(
            customer=customer, status=Invoice.Status.UNPAID, date_due=due,
        )
        InvoiceItem.objects.create(
            invoice=invoice, description="DEMO Fibre 50/50 (Monthly)",
            quantity=1, unit_price=Decimal(amount), tax_rate_pct=Decimal("0"),
        )
        invoice.recalc_totals()
        return customer

    def _simulate_day(self, customer, settings, day, due, captured, full):
        customer.refresh_from_db()
        config = CustomerBillingConfig.for_customer(customer)
        before = len(captured)

        warned = recurring._process_suspension_warning(
            customer, config, settings, day, commit=True
        )
        suspended = recurring._process_blocking(
            customer, config, settings, day, commit=True
        )

        label = f"  {day}"
        if day == due:
            label += "  (payment due)"
        events = captured[before:]

        if not events and not suspended:
            block_date = recurring.suspension_block_date(customer, config, day)
            days_left = (block_date - day).days if block_date else None
            note = "nothing sent"
            if days_left is not None and days_left > 0:
                note += f"  (cut-off in {days_left} day(s) -- outside the notice period"
                note += ", or already warned about this date)"
            self.stdout.write(f"{label}  {note}")
            return

        self.stdout.write(self.style.SUCCESS(label))
        for email in events:
            self._render(email, full)
        if suspended:
            customer.refresh_from_db()
            statuses = list(
                Service.objects.filter(customer=customer).values_list("status", flat=True)
            )
            self.stdout.write(f"      SERVICES NOW: {', '.join(statuses)}")

    def _render(self, email, full):
        from notifications.services import build_context

        template = EmailTemplate.objects.filter(key=email.template_key).first()
        if template is None:
            self.stdout.write(self.style.ERROR(
                f"      [{email.template_key}] NO TEMPLATE -- nothing would be sent"
            ))
            return
        context = Context(
            build_context(email.template_key, email.customer, extra=email.extra_context)
        )
        subject = Template(template.subject).render(context)
        body = Template(template.body_html).render(context)

        self.stdout.write(f"      EMAIL: {email.template_key}")
        self.stdout.write(f"      SUBJECT: {subject}")
        if email.extra_context:
            self.stdout.write(f"      CONTEXT: {email.extra_context}")
        if full:
            self.stdout.write("      ---")
            for line in _plain(body).splitlines():
                self.stdout.write(f"      {line}")
            self.stdout.write("      ---")
        else:
            for line in _plain(body).splitlines():
                if "suspend" in line.lower() or "day" in line.lower():
                    self.stdout.write(f"      > {line}")

    def _summary(self, customer, captured, warning_days, blocking_days):
        warnings = [e for e in captured if e.template_key == EmailTemplate.Key.SUSPENSION_WARNING]
        suspensions = [e for e in captured if e.template_key == EmailTemplate.Key.SUSPENSION]

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("WHAT THE CUSTOMER ACTUALLY GOT"))
        self.stdout.write(f"  Suspension warnings: {len(warnings)}")
        self.stdout.write(f"  Suspension notices:  {len(suspensions)}")
        self.stdout.write("")
        expected = max(0, min(warning_days, blocking_days - 1))
        self.stdout.write(
            f"  A daily countdown: one warning on each day of the notice period, with the\n"
            f"  days remaining falling as the cut-off approaches. Nothing goes out on the\n"
            f"  due date itself -- nobody is late yet on the day a payment falls due.\n"
            f"  For a {blocking_days}-day blocking period and a {warning_days}-day notice "
            f"period that is {expected} warning(s)."
        )
        if len(warnings) != expected:
            self.stdout.write(self.style.ERROR(
                f"  MISMATCH: expected {expected}, got {len(warnings)}. Worth looking at."
            ))

    # --- tidying up -------------------------------------------------------

    def _cleanup_existing(self):
        for existing in Customer.objects.filter(customer_id=DEMO_REFERENCE):
            self._cleanup(existing)

    def _cleanup(self, customer):
        with transaction.atomic():
            Invoice.objects.filter(customer=customer).delete()
            Service.objects.filter(customer=customer).delete()
            customer.delete()
        Tariff.objects.filter(name="DEMO Fibre 50/50", services__isnull=True).delete()


def _plain(html):
    from django.utils.html import strip_tags

    text = html.replace("</p>", "</p>\n").replace("<br>", "\n")
    return "\n".join(line.strip() for line in strip_tags(text).splitlines() if line.strip())
