"""The recurring-billing engine: for a given date, works out which
opted-in customers (CustomerBillingConfig.billing_enabled=True) are due a
new invoice/pro forma, which unpaid invoices are due a reminder, and which
overdue customers should have their services auto-suspended -- then either
just counts what it would do (commit=False, a "Preview") or actually does
it and logs a RecurringBillingRun (commit=True, a "Run").

Deliberately simple/staff-triggered for this release -- see the design
notes in RecurringBillingRun's docstring and the ADR-style comments below
on a few judgment calls (minimum_balance's interaction with blocking,
reminder day-offset direction, and the single-cycle invoice-vs-pro-forma
choice) that weren't spelled out in the original request and were
resolved with a documented best guess rather than guessed silently.
"""
import calendar
import logging
from datetime import timedelta

from django.db import transaction
from django.db.models import Count, Min, Q
from django.utils import timezone

from customers.models import Customer
from notifications.models import EmailLog, EmailTemplate
from notifications.services import send_customer_email
from .models import (
    Invoice, InvoiceItem, Service, CustomerBillingConfig, ReminderSettings, SuspensionSettings,
    RecurringBillingRun,
)

logger = logging.getLogger(__name__)

_PERIOD_MONTHS = {"monthly": 1, "quarterly": 3, "biannually": 6, "annually": 12}


def _send_after_commit(template_key, customer, **kwargs):
    """Hand an email to the mailer only once the surrounding transaction
    has actually committed.

    This is the difference between a customer receiving an invoice and a
    customer receiving an invoice that does not exist. SMTP has no
    rollback: if the send happens inside the transaction and the
    transaction then fails, the PDF is already in their inbox quoting a
    number that no longer exists in the database -- and which the next run
    will hand to somebody else.

    on_commit also means a rolled-back savepoint discards the queued send,
    which is exactly the behaviour we want per customer.

    The send is wrapped so that a mailer failure can never fail the
    billing work that already committed. send_customer_email swallows its
    own exceptions today, but that is its choice to make, not something
    this module should depend on -- an invoice that exists with no email
    is recoverable, the reverse is not.
    """
    def _send():
        try:
            send_customer_email(template_key, customer, **kwargs)
        except Exception:  # noqa: BLE001 -- see docstring: never fail committed billing over an email
            logger.exception(
                "Failed to send %s email to customer %s after billing committed", template_key, customer.pk
            )

    transaction.on_commit(_send)


def _add_period(base_date, period):
    """base_date advanced by one payment_period, clamping the day to
    whatever the target month actually has (e.g. the 31st -> the 28th/29th
    in February) -- billing_day itself is capped 1-28 so this only ever
    matters for the anniversary-of-creation path."""
    months = _PERIOD_MONTHS.get(period, 1)
    month_index = base_date.month - 1 + months
    year = base_date.year + month_index // 12
    month = month_index % 12 + 1
    day = min(base_date.day, calendar.monthrange(year, month)[1])
    return base_date.replace(year=year, month=month, day=day)


def _billing_due_day(customer, config):
    if config.use_date_of_customer_creation:
        return customer.created_at.day
    return config.billing_day


def _invoice_due(customer, config, run_date):
    if not config.auto_create_invoices:
        return False
    if config.next_billing_date:
        return run_date >= config.next_billing_date
    return run_date.day == _billing_due_day(customer, config)


def _generate_document(customer, config, run_date, run, commit):
    """Returns "invoice", "proforma", or None (nothing to bill -- no
    active services). Only writes anything when commit=True.

    Judgment call: auto_create_invoices and auto_proforma_enabled share
    ONE cycle here (billing_day/next_billing_date) rather than each
    running on its own independent schedule -- the reference this was
    modeled on has separate day/period settings for pro formas, which
    would need its own next-proforma-date tracking to do properly. That's
    flagged as a follow-up rather than built here; auto_proforma_enabled
    just switches what TYPE of document this cycle produces.
    """
    active_services = list(customer.services.filter(status=Service.Status.ACTIVE).select_related("tariff"))
    if not active_services:
        return None

    doc_type = "proforma" if config.auto_proforma_enabled else "invoice"
    if not commit:
        return doc_type

    status_value = Invoice.Status.PROFORMA if config.auto_proforma_enabled else Invoice.Status.UNPAID
    date_due = run_date + timedelta(days=config.payment_due_days or 0)
    invoice = Invoice.objects.create(
        customer=customer, status=status_value, date_due=date_due, created_by_run=run
    )
    for svc in active_services:
        InvoiceItem.objects.create(
            invoice=invoice, item_type=InvoiceItem.ItemType.TARIFF, tariff=svc.tariff, service=svc,
            description=f"{svc.tariff.name} ({svc.tariff.get_billing_period_display()})",
            quantity=1, unit_price=svc.tariff.price, tax_rate_pct=svc.tariff.tax_rate_pct,
        )
    invoice.recalc_totals()

    # Mirrors the Splynx reference's "Debit" ledger entry. This used to be
    # an inline `customer.balance + invoice.total` here and ONLY here, with
    # a note that manually-created invoices were unaffected -- while
    # payments credited the balance regardless of which kind of invoice
    # they settled, so the ledger drifted on every hand-raised invoice.
    # Invoice.apply_balance_debit is now the single implementation, called
    # from every path that can change whether an invoice is owed, and it
    # tracks what it did in Invoice.balance_debited so the debit can be
    # reversed on a delete or a cancellation.
    invoice.apply_balance_debit()

    if config.send_billing_notifications:
        _send_after_commit(doc_type, customer, invoice=invoice)

    config.next_billing_date = _add_period(run_date, config.payment_period)
    config.save(update_fields=["next_billing_date"])
    return doc_type


def _process_reminders(customer, config, reminder_settings, run_date, commit):
    """Judgment call: reminder_N_day is read as "N days BEFORE the due
    date" (the far more common convention, and consistent with the
    reference's own values -- reminder #1 further out, #2 closer in)
    rather than days after. Sends at most once per (customer, day) even if
    multiple invoices qualify, and skips re-sending if this customer's
    already gotten a payment_reminder email today (so re-running Preview
    -> Run twice in one day can't double-send)."""
    if not config.reminder_enabled:
        return 0

    slots = (
        (config.reminder_1_day, reminder_settings.reminder_1_enabled),
        (config.reminder_2_day, reminder_settings.reminder_2_enabled),
        (config.reminder_3_day, reminder_settings.reminder_3_enabled),
    )
    unpaid = customer.invoices.filter(status__in=[Invoice.Status.UNPAID, Invoice.Status.OVERDUE])
    due_invoice = None
    for invoice in unpaid:
        days_until_due = (invoice.date_due - run_date).days
        for day_value, enabled in slots:
            if day_value is not None and enabled and days_until_due == day_value:
                due_invoice = invoice
                break
        if due_invoice:
            break

    if due_invoice is None:
        return 0
    if not commit:
        return 1

    # Real wall-clock "today", not run_date -- run_date is whatever date is
    # being *simulated* (a backdated catch-up Run is allowed), but the
    # thing this guards against is literally clicking Run twice for the
    # same invocation, which always happens on the same real calendar day
    # regardless of which run_date was picked. Comparing against run_date
    # instead would let two backdated Runs for the same run_date (done on
    # two different real days) double-send.
    already_sent_today = EmailLog.objects.filter(
        customer=customer, template_key=EmailTemplate.Key.PAYMENT_REMINDER, created_at__date=timezone.localdate()
    ).exists()
    if already_sent_today:
        return 0
    _send_after_commit(EmailTemplate.Key.PAYMENT_REMINDER, customer, invoice=due_invoice)
    return 1


def blocking_candidate_services(customer, config, run_date):
    """The ACTIVE services that would be suspended if a billing run happened
    on `run_date`. An empty list means this customer would not be blocked.

    Judgment call (unchanged): blocking requires BOTH a real invoice overdue
    past blocking_period_days AND the customer's balance being worse than
    minimum_balance -- the reference's "minimum balance" field reads as a
    credit cushion that can excuse a technically-overdue invoice, not as a
    second independent trigger.

    Deliberately does NOT consult SuspensionSettings.auto_suspend_enabled.
    That master switch belongs to the caller:

      * _process_blocking honours it, so nothing is ever suspended while
        it's off.
      * upcoming_blocks (the dashboard panel) deliberately ignores it, and
        reports its state separately, so the panel still shows who is heading
        for a cut-off while the automation is disarmed. Gating it there would
        leave the panel reading zero forever and telling you nothing.

    Extracted from _process_blocking so that the dashboard and the real
    billing run share ONE definition of "would be blocked". Two copies of
    this predicate would drift, and a dashboard that quietly disagrees with
    what the run does is worse than no dashboard.
    """
    if config.blocking_period_days is None:
        return []
    if customer.balance <= config.minimum_balance:
        return []

    cutoff = run_date - timedelta(days=config.blocking_period_days)
    has_overdue = customer.invoices.filter(
        status__in=[Invoice.Status.UNPAID, Invoice.Status.OVERDUE], date_due__lte=cutoff
    ).exists()
    if not has_overdue:
        return []

    return list(customer.services.filter(status=Service.Status.ACTIVE))


def _process_blocking(customer, config, suspension_settings, run_date, commit):
    """Suspends every ACTIVE service by writing through the normal .save()
    path, so radiusauth's existing signal keeps doing exactly what it already
    does for a manual suspension (walled-garden PPPoE routing, hard-reject
    for OVPN).

    Gated first on SuspensionSettings.auto_suspend_enabled -- the platform-
    wide master switch (Configs -> Billing -> Auto-suspension). Off by
    default, and checked before anything else here, so flipping it off is
    always a guaranteed "nobody gets suspended by this run," regardless of
    what any individual customer's blocking_period_days says."""
    if not suspension_settings.auto_suspend_enabled:
        return 0

    active_services = blocking_candidate_services(customer, config, run_date)
    if not active_services:
        return 0
    if not commit:
        return 1

    for svc in active_services:
        svc.status = Service.Status.SUSPENDED
        svc.save()
    if config.send_billing_notifications:
        _send_after_commit(EmailTemplate.Key.SUSPENSION, customer)
    return 1


def upcoming_blocks(from_date, horizon_days=7, customers=None):
    """Who is heading for a block, and on which date -- the dashboard's
    "Blocking tomorrow" panel.

    Returns a list of dicts, one per customer, each carrying the EARLIEST run
    date within the horizon on which that customer would be blocked. Sorted
    soonest-first, then by largest balance owed.

    `from_date` is normally tomorrow. `customers` accepts a pre-scoped
    queryset so the caller can apply partner visibility (see
    CustomerViewSet.get_queryset) -- a reseller-scoped staff member must not
    learn the names of customers outside their partners just because those
    customers appear on a dashboard panel.

    Does NOT consider SuspensionSettings.auto_suspend_enabled; the caller
    reports that separately. See blocking_candidate_services for why.

    HOW THE DATE IS DERIVED. Rather than re-running the predicate once per
    day per customer (which would be thousands of queries), this inverts it.
    blocking_candidate_services requires

        date_due <= run_date - blocking_period_days

    which rearranges to

        run_date >= date_due + blocking_period_days

    so the earliest run date on which a customer qualifies is simply their
    OLDEST unpaid/overdue invoice's due date plus their blocking period. The
    predicate is monotonic in run_date -- the cutoff only moves later, so
    more invoices qualify, never fewer -- which is what makes that inversion
    valid. The other two conditions (balance, having active services) don't
    depend on the date at all.

    A customer whose date has already passed is reported as `from_date`: they
    are overdue for a block and the very next run will catch them. There is a
    test that checks this fast path agrees with calling
    blocking_candidate_services day by day, because this optimisation is only
    safe while that monotonicity holds.
    """
    if customers is None:
        customers = Customer.objects.all()

    candidates = (
        customers.filter(billing_config__billing_enabled=True)
        .select_related("billing_config")
        .annotate(
            active_service_count=Count(
                "services", filter=Q(services__status=Service.Status.ACTIVE), distinct=True
            )
        )
    )

    horizon_end = from_date + timedelta(days=max(1, horizon_days) - 1)

    # One aggregate for every customer's oldest still-owing invoice, rather
    # than a query each.
    oldest_due = {}
    overdue_counts = {}
    rows = (
        Invoice.objects.filter(
            status__in=[Invoice.Status.UNPAID, Invoice.Status.OVERDUE],
            customer__in=candidates.values("pk"),
        )
        .values("customer_id")
        .annotate(earliest=Min("date_due"), owing=Count("id"))
    )
    for row in rows:
        oldest_due[row["customer_id"]] = row["earliest"]
        overdue_counts[row["customer_id"]] = row["owing"]

    results = []
    for customer in candidates:
        config = customer.billing_config
        if config.blocking_period_days is None:
            continue
        if customer.balance <= config.minimum_balance:
            continue
        if not customer.active_service_count:
            continue
        due = oldest_due.get(customer.pk)
        if due is None:
            continue

        block_date = due + timedelta(days=config.blocking_period_days)
        if block_date < from_date:
            # Already past the threshold -- the next run blocks them.
            block_date = from_date
        if block_date > horizon_end:
            continue

        results.append({
            "customer": customer.pk,
            "reference": customer.customer_id,
            "name": customer.full_name,
            "block_date": block_date,
            "days_until": (block_date - from_date).days,
            "balance": customer.balance,
            "oldest_invoice_due": due,
            "invoices_owing": overdue_counts.get(customer.pk, 0),
            "active_services": customer.active_service_count,
            "blocking_period_days": config.blocking_period_days,
        })

    results.sort(key=lambda r: (r["block_date"], -r["balance"]))
    return results


_COUNT_KEYS = ("invoices_created", "proforma_invoices_created", "reminders_sent", "suspensions_applied")


def _process_one_customer(customer, config, run, run_date, reminder_settings, suspension_settings, commit):
    """Everything owed to a single customer on this run, and what it came
    to. Returns its own counts rather than incrementing a shared tally, so
    the caller can throw the numbers away if this customer's transaction
    doesn't survive -- a count for a rolled-back invoice is a lie the
    History row would otherwise tell forever.
    """
    counts = dict.fromkeys(_COUNT_KEYS, 0)
    if _invoice_due(customer, config, run_date):
        doc_type = _generate_document(customer, config, run_date, run, commit)
        if doc_type == "invoice":
            counts["invoices_created"] += 1
        elif doc_type == "proforma":
            counts["proforma_invoices_created"] += 1
    counts["reminders_sent"] += _process_reminders(customer, config, reminder_settings, run_date, commit)
    counts["suspensions_applied"] += _process_blocking(customer, config, suspension_settings, run_date, commit)
    return counts


def _describe_failures(failures):
    """A status_message that names names. "3 customers failed" sends
    somebody digging through logs; naming the first few means the person
    reading History can go straight to them. Capped to fit the column."""
    names = ", ".join(f"{customer.customer_id} ({exc.__class__.__name__})" for customer, exc in failures[:3])
    more = len(failures) - 3
    message = f"{len(failures)} customer(s) failed and were skipped: {names}"
    if more > 0:
        message += f", and {more} more"
    return message[:255]


def run_recurring_billing(run_date, partner_ids=None, commit=False, triggered_by=None):
    """Core entry point, used by both the Preview and Run API actions (and
    eventually a crontab-scheduled management command). commit=False never
    writes anything -- not an invoice, not an email, not a RecurringBillingRun
    row -- it only counts what WOULD happen.

    commit=True gives EACH CUSTOMER their own transaction, and that is the
    important part. This used to run every customer inside one atomic
    block, which meant a single bad row -- a dropped connection, a
    numbering clash, one malformed tariff -- rolled back every invoice the
    run had already created, for everybody, while the invoice emails
    (which are not transactional) had already gone out. The failure mode
    was hundreds of customers holding a PDF for an invoice that no longer
    existed, and a History row cheerfully reporting how many invoices it
    had created.

    Now a customer who fails is rolled back, logged, named in the run's
    status_message, and stepped over. Everyone else keeps their invoice.
    A run with any casualties is marked FAILED -- partial success is still
    something somebody has to look at -- but the counts on it describe
    only what actually committed."""
    # Booked tariff changes first: a service switching plans today must bill
    # on the NEW tariff in this same run, not next month's. Idempotent and
    # also available as its own cron command -- see billing.tariff_changes.
    if commit:
        from .tariff_changes import apply_due_tariff_changes

        apply_due_tariff_changes(as_of=run_date)
        # Before invoicing, not after: a customer whose service ended today
        # should not be handed one more invoice on the way out.
        from .cancellations import apply_due_cancellations

        apply_due_cancellations(as_of=run_date, commit=True)

    customers_qs = Customer.objects.filter(billing_config__billing_enabled=True).select_related("billing_config")
    if partner_ids:
        customers_qs = customers_qs.filter(partner_id__in=partner_ids)

    counts = dict.fromkeys(_COUNT_KEYS, 0)
    status = RecurringBillingRun.Status.PROCESSED
    status_message = ""
    reminder_settings = ReminderSettings.load()
    suspension_settings = SuspensionSettings.load()

    if not commit:
        for customer in customers_qs:
            for key, value in _process_one_customer(
                customer, customer.billing_config, None, run_date,
                reminder_settings, suspension_settings, commit=False,
            ).items():
                counts[key] += value
        return {"counts": counts, "status": status, "status_message": status_message, "run": None}

    # Created and committed up front, before any customer is touched, so
    # that every invoice this run generates has a run to point at and the
    # run survives whatever happens next. A run row that only appears if
    # the work succeeds is a run row you can't find when you most need it.
    run = RecurringBillingRun.objects.create(run_date=run_date, triggered_by=triggered_by)
    if partner_ids:
        run.partners.set(partner_ids)

    failures = []
    for customer in customers_qs:
        try:
            with transaction.atomic():
                customer_counts = _process_one_customer(
                    customer, customer.billing_config, run, run_date,
                    reminder_settings, suspension_settings, commit=True,
                )
        except Exception as exc:  # noqa: BLE001 -- one bad customer must not end the run for everybody else
            logger.exception("Recurring billing failed for customer %s; skipping", customer.pk)
            failures.append((customer, exc))
            continue
        # Only reached once this customer's transaction has committed, so
        # these numbers describe work that actually exists.
        for key, value in customer_counts.items():
            counts[key] += value

    if failures:
        status = RecurringBillingRun.Status.FAILED
        status_message = _describe_failures(failures)

    run.status = status
    run.status_message = status_message
    run.invoices_created_count = counts["invoices_created"]
    run.proforma_invoices_created_count = counts["proforma_invoices_created"]
    run.reminders_sent_count = counts["reminders_sent"]
    run.suspensions_applied_count = counts["suspensions_applied"]
    run.save(update_fields=[
        "status", "status_message",
        "invoices_created_count", "proforma_invoices_created_count",
        "reminders_sent_count", "suspensions_applied_count",
    ])

    return {"counts": counts, "status": status, "status_message": status_message, "run": run}
