"""Send invoices that predate the integration to the Conecto app.

The push signal only fires when an invoice is saved, so everything raised
before the integration went live is invisible in the client's app. This
sends them deliberately.

    python manage.py conecto_backfill_invoices --client-code SMF463
    python manage.py conecto_backfill_invoices --client-code SMF463 --apply
    python manage.py conecto_backfill_invoices --client-code SMF463 --include-paid --apply
    python manage.py conecto_backfill_invoices --all --since 2026-01-01 --apply

DRY RUN BY DEFAULT, and not as a formality.

Every invoice sent here appears in a real customer's app, and Conecto fire
a push notification per record. A whole billing history pushed at once is a
phone buzzing thirty times about invoices the customer settled months ago.
That is a decision somebody should make on purpose, having seen the list.

UNPAID AND OVERDUE ONLY, unless you ask otherwise.

Those are the ones a customer can act on. Paid history is a nice-to-have
and the expensive half of the notification problem, so --include-paid
exists and is not the default.

Safe to re-run. Each invoice goes through the same path the signal uses,
which skips anything already sent with unchanged figures.
"""
from django.core.management.base import BaseCommand
from django.utils.dateparse import parse_date


class Command(BaseCommand):
    help = "Push pre-existing invoices to Conecto (dry run unless --apply)."

    def add_arguments(self, parser):
        parser.add_argument("--client-code", help="One customer's client code, e.g. SMF463.")
        parser.add_argument(
            "--all", action="store_true",
            help="Every customer. Use --since with this unless you mean the whole history.",
        )
        parser.add_argument(
            "--include-paid", action="store_true",
            help="Also send settled invoices. Off by default: a customer cannot act on them "
                 "and each one is another notification.",
        )
        parser.add_argument(
            "--since", help="Only invoices due on or after this date (YYYY-MM-DD).",
        )
        parser.add_argument(
            "--limit", type=int, help="Stop after this many. Useful for a first, small run.",
        )
        parser.add_argument(
            "--apply", action="store_true", help="Actually send. Without it, nothing leaves.",
        )
        # Conecto, 7 Oct 2026: "please pace it to roughly 10 calls per second
        # or less". 8 by default leaves headroom.
        parser.add_argument(
            "--quiet", action="store_true", help="Totals only, not one line per invoice.",
        )
        parser.add_argument(
            "--rate", type=float, default=8.0,
            help="Most calls per second (default 8; Conecto asked for 10 or less).",
        )

    def handle(self, *args, **options):
        from billing.models import Invoice
        from conecto import client
        from conecto.models import ConectoInvoice
        from conecto.signals import _send_invoice
        from customers.models import Customer

        if not options["client_code"] and not options["all"]:
            self.stderr.write("Give --client-code, or --all if you really mean everybody.")
            return

        statuses = [Invoice.Status.UNPAID, Invoice.Status.OVERDUE]
        if options["include_paid"]:
            statuses.append(Invoice.Status.PAID)

        invoices = Invoice.objects.select_related("customer").filter(status__in=statuses)

        if options["client_code"]:
            customer = Customer.objects.filter(
                customer_id__iexact=options["client_code"].strip()
            ).first()
            if customer is None:
                self.stderr.write(f'No customer with client code "{options["client_code"]}".')
                return
            invoices = invoices.filter(customer=customer)
            self.stdout.write(f"Customer: {customer.full_name} ({customer.customer_id})")

        if options["since"]:
            since = parse_date(options["since"])
            if since is None:
                self.stderr.write("--since must be YYYY-MM-DD.")
                return
            invoices = invoices.filter(date_due__gte=since)

        # Credit notes share this table: CN number, negative total, status
        # "paid". client.push_invoice refuses them, so counting them here
        # as "would be sent" would be the dry run telling a lie about what
        # is going to happen -- which is the one thing a dry run must not
        # do.
        from django.db.models import Q

        credit_notes = invoices.filter(Q(total__lt=0) | Q(number__istartswith="CN"))
        credit_count = credit_notes.count()
        invoices = invoices.exclude(pk__in=credit_notes.values("pk"))

        # Already-sent ones are listed as skipped rather than silently
        # dropped: "nothing happened" and "nothing needed to happen" look
        # identical in a log, and only one of them is reassuring.
        already = set(
            ConectoInvoice.objects.filter(invoice__in=invoices).values_list("invoice_id", flat=True)
        )

        invoices = invoices.order_by("date_due")
        if options["limit"]:
            invoices = invoices[: options["limit"]]

        rows = list(invoices)
        to_send = [i for i in rows if i.pk not in already]

        if not rows:
            if credit_count:
                self.stdout.write(
                    f"Nothing to send. {credit_count} credit note(s) matched and were "
                    "excluded -- Conecto have no credit state for them."
                )
            else:
                self.stdout.write("Nothing matches. Try --include-paid, or a wider --since.")
            return

        self.stdout.write("")
        for invoice in ([] if options["quiet"] else rows):
            mark = "  " if invoice.pk in already else "->"
            code = getattr(invoice.customer, "customer_id", "") or "(no client code)"
            self.stdout.write(
                f" {mark} {invoice.number:<22} {invoice.status:<8} "
                f"{invoice.total:>10} due {invoice.date_due}  {code}"
            )

        self.stdout.write("")
        self.stdout.write(f"matched:        {len(rows)}")
        if credit_count:
            self.stdout.write(
                f"credit notes:   {credit_count} excluded -- Conecto have no credit "
                "state, so these would show as negative bills"
            )
        self.stdout.write(f"already sent:   {len(rows) - len(to_send)}")
        self.stdout.write(f"would be sent:  {len(to_send)}")

        missing = [i for i in to_send if not getattr(i.customer, "customer_id", "")]
        if missing:
            self.stdout.write(self.style.WARNING(
                f"{len(missing)} have no client code and will be skipped -- Conecto key "
                "every record on it, so they would attach to nobody."
            ))

        if not to_send:
            self.stdout.write("Nothing to do.")
            return

        if not options["apply"]:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING(
                "Dry run. Each of these becomes a record in the client's app, and Conecto "
                "notify per record. Re-run with --apply when you are happy with the list."
            ))
            return

        if not client._enabled():
            self.stderr.write("The Conecto integration is switched off; nothing was sent.")
            return

        import time

        gap = 1.0 / max(0.1, min(options["rate"], 10.0))
        sent = 0
        for n, invoice in enumerate(to_send, 1):
            started = time.monotonic()
            _send_invoice(invoice.pk)
            if n % 100 == 0:
                self.stdout.write(f"  ... {n} of {len(to_send)}")
            spare = gap - (time.monotonic() - started)
            if spare > 0:
                time.sleep(spare)
            if ConectoInvoice.objects.filter(invoice_id=invoice.pk).exists():
                sent += 1
            else:
                # push_invoice never raises; a failure is a ConectoCall row.
                self.stdout.write(self.style.WARNING(
                    f"  {invoice.number} did not record a push -- see the Conecto call log, "
                    "and replay with conecto_resync."
                ))

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Sent {sent} of {len(to_send)}."))
