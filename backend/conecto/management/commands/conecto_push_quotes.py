"""Send quotes to the Conecto app (quoteCreated, 7 Oct 2026).

    python manage.py conecto_push_quotes            # dry run: what would go
    python manage.py conecto_push_quotes --apply

Quotes are sent by themselves whenever one is created, changed, converted or
cancelled. This command does what nothing else can:

* the FIRST send of the quotes that already existed, and
* EXPIRY -- a quote whose valid-until date has passed changes to "expired"
  without anybody saving it, so a nightly run notices it:

    30 0 * * * cd /home/ubuntu/isp-platform && docker compose exec -T backend \\
        python manage.py conecto_push_quotes --apply >/dev/null 2>&1

Only what changed since the last send goes out.
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Send new, changed and expired quotes to Conecto (dry run unless --apply)."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Actually send.")

    def handle(self, *args, **options):
        from billing.models import Invoice

        from conecto.models import ConectoQuote
        from conecto.signals import quote_status_for, sync_quote

        known = dict(ConectoQuote.objects.values_list("invoice_id", "pushed_status"))
        quotes = (
            Invoice.objects.select_related("customer")
            .prefetch_related("items")
            .filter(status=Invoice.Status.QUOTE)
            .exclude(customer__customer_id="")
            .order_by("number")
        )
        sent = would = failed = 0
        for invoice in quotes:
            status = quote_status_for(invoice)
            if known.get(invoice.pk) == status:
                continue
            if not options["apply"]:
                self.stdout.write(f"  would send  {invoice.number:<12} {invoice.customer.customer_id:<10} {status}")
                would += 1
                continue
            if sync_quote(invoice):
                sent += 1
            elif known.get(invoice.pk) != status:
                failed += 1
                self.stdout.write(f"  FAILED      {invoice.number:<12} {invoice.customer.customer_id}")
        if options["apply"]:
            self.stdout.write(f"Sent {sent}" + (f", failed {failed}" if failed else "") + ".")
        else:
            self.stdout.write(f"Would send {would}. Nothing was sent; add --apply.")
