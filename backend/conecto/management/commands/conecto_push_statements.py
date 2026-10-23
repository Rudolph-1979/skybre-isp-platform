"""Tell the Conecto app a monthly statement exists for each client.

    python manage.py conecto_push_statements                    # dry run, last month
    python manage.py conecto_push_statements --apply
    python manage.py conecto_push_statements --period 2026-09 --apply
    python manage.py conecto_push_statements --client-code SMF463 --apply

statementCreated "on a schedule" (agreed with Conecto; 7 Oct 2026). Run it
once a month, after the month has closed:

    15 7 1 * * cd /home/ubuntu/isp-platform && docker compose exec -T backend \\
        python manage.py conecto_push_statements --apply >/dev/null 2>&1

WHO: every client with billing switched on and a client code who either
had an invoice in that month or still has a balance (owing or in credit).
A client with nothing in the month and a zero balance has nothing to read.

ONCE PER CLIENT PER MONTH: a month already sent successfully is skipped,
so re-running is safe. The PDF itself is not sent -- Conecto ask for a
fresh link when the client taps (conecto/documents/statement-url/).

DRY RUN BY DEFAULT: lists who would get one and sends nothing.
"""
import datetime

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone


def previous_month(today):
    first = today.replace(day=1)
    return (first - datetime.timedelta(days=1)).strftime("%Y-%m")


class Command(BaseCommand):
    help = "Push statementCreated for last month's statements to Conecto (dry run unless --apply)."

    def add_arguments(self, parser):
        parser.add_argument("--period", help="YYYY-MM. Defaults to last month.")
        parser.add_argument("--client-code", help="One client only, e.g. SMF463.")
        parser.add_argument("--apply", action="store_true", help="Actually send.")

    def handle(self, *args, **options):
        from billing.models import Invoice
        from customers.models import Customer

        from conecto import client
        from conecto.models import ConectoCall

        period = options["period"] or previous_month(timezone.localdate())
        try:
            start = datetime.datetime.strptime(period, "%Y-%m").date()
        except ValueError:
            self.stderr.write("--period must look like 2026-09.")
            return
        end = (start + datetime.timedelta(days=32)).replace(day=1)

        invoiced = Invoice.objects.filter(
            date_issued__gte=start, date_issued__lt=end, status__in=Invoice.ISSUED_STATUSES,
        ).values("customer_id")
        customers = (
            Customer.objects.filter(billing_config__billing_enabled=True)
            .exclude(customer_id="")
            .filter(Q(pk__in=invoiced) | ~Q(balance=0))
            .order_by("customer_id")
        )
        if options["client_code"]:
            customers = customers.filter(customer_id__iexact=options["client_code"].strip())

        sent_already = set(
            ConectoCall.objects.filter(
                endpoint="statementCreated", status=ConectoCall.Status.OK, subject_kind="statement",
                subject_ref__endswith=f" {period}",
            ).values_list("subject_ref", flat=True)
        )

        done = failed = skipped = 0
        for customer in customers:
            ref = f"{customer.customer_id} {period}"
            if ref in sent_already:
                skipped += 1
                continue
            if not options["apply"]:
                self.stdout.write(f"  would send  {customer.customer_id:<10} {customer.full_name}")
                done += 1
                continue
            if client.push_statement(customer, period) is None:
                failed += 1
                self.stdout.write(f"  FAILED      {customer.customer_id:<10} {customer.full_name}")
            else:
                done += 1
        verb = "sent" if options["apply"] else "would send"
        self.stdout.write(
            f"{period}: {verb} {done}, already sent {skipped}" + (f", failed {failed}" if failed else "") + "."
        )
        if not options["apply"] and done:
            self.stdout.write("Nothing was sent. Add --apply when the list above looks right.")
