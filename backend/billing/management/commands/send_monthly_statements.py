"""Email last month's statement to customers who had a month worth reporting.

    python manage.py send_monthly_statements                    # dry run
    python manage.py send_monthly_statements --apply
    python manage.py send_monthly_statements --period 2026-08 --apply
    python manage.py send_monthly_statements --client-code SMF463 --apply

Runs on the 2nd, after the 1st has invoiced:

    0 6 2 * * cd /home/ubuntu/isp-platform && docker compose exec -T backend \\
        python manage.py send_monthly_statements --apply >> logs/billing.log 2>&1

WHO GETS ONE, AND WHY IT IS NOT billing_enabled

Any customer with a client code who had movements in the month or who
carries a balance at the end of it.

NOT `CustomerBillingConfig.billing_enabled`, which is what the first
version of this used and which was wrong. That switch governs whether the
recurring engine invoices, reminds and suspends somebody -- it is about
AUTOMATION, and says nothing about whether they have an account worth
reporting on. A customer invoiced by hand has a ledger like anybody else.
Keyed on that flag, the customer here with thirty invoices and two years
of imported payments received nothing, while two accounts with no activity
at all received blank pages.

A customer with no movements AND no balance is skipped: there is nothing
on the page, and a document saying so is not a document.

WHAT THE DOCUMENT COVERS, AND WHAT --period MEANS

The statement is a ROLLING one: the last twelve months of movements,
closing at TODAY'S balance. It is not a calendar month.

The first version was a calendar month and closed at the month end, which
put the wrong number on the page. Sent on the 2nd it reported what was
owed on the last day of the previous month -- and by then the 1st had
already invoiced, so the closing balance disagreed with the account the
moment it landed. Closing at today makes the foot of the page and the
figure we are chasing the same figure.

So --period NO LONGER SAYS WHAT IS ON THE DOCUMENT. It is the run key:
the month this run counts as, which is what stops a second run in the
same month sending everybody a second statement, and what --resend
overrides. Left off, it is the month that has just closed, so the job on
the 2nd of October is the "2026-09" run.

Anything older than twelve months is collapsed into the brought-forward
line rather than dropped -- the balance is unaffected, only the detail
is -- so the document stays a page or two however long the account has
run. The full history is still the Account Statement.

THE RECONCILIATION IS A GATE, NOT A NOTE.

billing.statements derives the opening balance from dated records, because
Customer.balance is a stored running total that cannot say what it was on
the 1st. Where that derivation disagrees with the stored balance, this
command REFUSES TO SEND and says whose. A statement whose closing figure
contradicts what the customer is being chased for is not a smaller problem
than no statement -- it is a dispute, in writing, with our name on it.

WHAT THE GATE CANNOT CATCH, and it is worth knowing: records that are
wrong in the SAME direction as the balance. A payment captured twice moves
the derived figure and the stored one identically, so it reconciles
perfectly and the customer gets a statement showing credit they may never
have paid. This command checks that the books are self-consistent. Only a
bank reconciliation checks that they are true.

`manage.py balance_drift` reports the same disagreement in bulk.

DRY RUN BY DEFAULT, because this sends real email to every customer at
once and the first time it runs is the only time that is true.

ONE STATEMENT BUILT PER CUSTOMER, which is a few queries each. Fine for a
monthly job over a customer base of this size; if it ever runs over tens
of thousands, the selection wants narrowing before the loop rather than
inside it.
"""
import datetime
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

ZERO = Decimal("0.00")


class Command(BaseCommand):
    help = "Email last month's statement (dry run unless --apply)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--period",
            help="Run key as YYYY-MM -- which month this run counts as, NOT what "
                 "the statement covers. Defaults to the month just ended.",
        )
        parser.add_argument(
            "--client-code", help="One customer only, e.g. SMF463.",
        )
        parser.add_argument(
            "--limit", type=int, help="Stop after this many. Useful for a first, small run.",
        )
        parser.add_argument(
            "--resend", action="store_true",
            help="Send again to customers already sent this period.",
        )
        parser.add_argument(
            "--apply", action="store_true", help="Actually send. Without it, nothing leaves.",
        )

    def handle(self, *args, **options):
        from billing import statements
        from billing.models import CustomerBillingConfig
        from customers.models import Customer
        from notifications.models import EmailLog, EmailTemplate
        from notifications.services import send_customer_email

        period = options.get("period")
        if period:
            try:
                anchor = datetime.datetime.strptime(period, "%Y-%m").date()
            except ValueError:
                self.stderr.write(f'--period must look like 2026-09, not "{period}".')
                return
        else:
            # The month that has just closed, not the one in progress.
            first_of_this_month = timezone.localdate().replace(day=1)
            anchor = first_of_this_month - datetime.timedelta(days=1)
            period = anchor.strftime("%Y-%m")

        if not EmailTemplate.objects.filter(key=EmailTemplate.Key.STATEMENT).exists():
            self.stderr.write(
                "There is no Statement email template. Nothing would be sent, and a "
                "run that reports success having sent nothing is worse than this line."
            )
            return

        customers = (
            Customer.objects.exclude(customer_id="")
            .exclude(customer_id__isnull=True)
            .select_related("billing_config")
            .order_by("customer_id")
        )
        if options["client_code"]:
            code = options["client_code"].strip()
            customers = customers.filter(customer_id__iexact=code)
            if not customers.exists():
                self.stderr.write(f'No customer with client code "{code}".')
                return

        sent = already = failed = nothing_to_report = 0
        unreconciled = []
        no_email = []

        # One `as_at` for the whole run, so a job that starts at 23:59 does
        # not put two different dates on two customers' statements.
        as_at = timezone.localdate()
        self.stdout.write(
            f"Statements as at {as_at.strftime('%d/%m/%Y')} "
            f"({statements.ROLLING_MONTHS} months of movements), "
            f"run {anchor.strftime('%B %Y')}\n"
        )

        for customer in customers:
            if options["limit"] is not None and sent >= options["limit"]:
                break
            config = getattr(customer, "billing_config", None)

            statement = statements.rolling_statement(customer, as_at=as_at)

            if not statement["lines"] and statement["closing_balance"] == ZERO:
                # Nothing happened and nothing is owed. There is no page to
                # send, and a document saying so is not a document.
                nothing_to_report += 1
                continue

            check = statement["reconciliation"]
            if not check["reconciles"]:
                # NOT SENT. See this module's docstring: a closing figure
                # that disagrees with the account is a dispute in writing.
                unreconciled.append((customer, check))
                continue

            if config is not None and config.statement_sent_for == period \
                    and not options["resend"]:
                already += 1
                continue

            if not (customer.email or "").strip():
                no_email.append(customer)
                continue

            self.stdout.write(
                f"  {(customer.customer_id or '-'):<12} {customer.full_name[:28]:<28} "
                f"opening {statement['opening_balance']:>10,.2f}  "
                f"closing {statement['closing_balance']:>10,.2f}  "
                f"{len(statement['lines'])} line(s)"
            )

            if not options["apply"]:
                sent += 1
                continue

            log = send_customer_email(
                EmailTemplate.Key.STATEMENT, customer, period=period,
                extra_context={
                    "statement_period": statement["period_label"],
                    "statement_opening_balance": statement["opening_balance"],
                    "statement_closing_balance": statement["closing_balance"],
                },
            )
            if log is not None and log.status == EmailLog.Status.SENT:
                # Recorded only once the mailer says it went. A period
                # marked sent after a mail outage is a statement nobody
                # receives and nobody re-sends.
                #
                # The config row is created here if this customer has never
                # had one -- selection no longer requires it, but the record
                # of what was sent has to live somewhere, and
                # CustomerBillingConfig is where per-customer billing state
                # already lives. `for_customer` never touches
                # billing_enabled, so this does not opt anybody into the
                # recurring engine by side effect.
                if config is None:
                    config = CustomerBillingConfig.for_customer(customer)
                CustomerBillingConfig.objects.filter(pk=config.pk).update(
                    statement_sent_for=period
                )
                sent += 1
            else:
                failed += 1
                self.stderr.write(
                    f"    FAILED {customer.customer_id}: "
                    f"{getattr(log, 'error_message', '') or 'see the email log'}"
                )

        verb = "sent" if options["apply"] else "would send"
        line = f"\n{period}: {verb} {sent}"
        if already:
            line += f", {already} already sent this period"
        if nothing_to_report:
            line += f", {nothing_to_report} with no activity and no balance"
        if failed:
            line += f", {failed} failed"
        self.stdout.write(line)

        if no_email:
            self.stdout.write(
                f"\n{len(no_email)} customer(s) have no email address and were skipped:"
            )
            for customer in no_email[:20]:
                self.stdout.write(f"  {customer.customer_id or '-'}  {customer.full_name}")

        if unreconciled:
            self.stdout.write(
                f"\n{len(unreconciled)} customer(s) NOT SENT -- their records do not "
                f"account for their balance, so a statement would contradict what they "
                f"are being chased for:"
            )
            for customer, check in unreconciled[:20]:
                self.stdout.write(
                    f"  {(customer.customer_id or '-'):<12} {customer.full_name[:28]:<28} "
                    f"stored {check['stored']:>10,.2f}  records {check['expected']:>10,.2f}  "
                    f"drift {check['drift']:>10,.2f}"
                )
            self.stdout.write("  Start with: manage.py balance_drift")

        if not options["apply"] and sent:
            self.stdout.write("\nNothing was sent. Add --apply when the list above looks right.")
