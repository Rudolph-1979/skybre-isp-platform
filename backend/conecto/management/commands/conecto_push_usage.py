"""Send each customer's month-to-date usage to the Conecto app.

    python manage.py conecto_push_usage                       # dry run, this month
    python manage.py conecto_push_usage --apply
    python manage.py conecto_push_usage --period 2026-08 --apply
    python manage.py conecto_push_usage --client-code SMF463 --apply
    python manage.py conecto_push_usage --resend --apply       # ignore the digest

Run it once a day on the VPS, after midnight so the figure a customer sees
in the morning covers a whole day:

    15 0 * * * cd /home/ubuntu/isp-platform && docker compose exec -T backend \\
        python manage.py conecto_push_usage --apply >/dev/null 2>&1

DRY RUN BY DEFAULT. Conecto have not confirmed the usageUpdated payload
shape, so the first real run is a decision somebody should make on purpose
having seen what would go out. `client.DEFAULT_URLS` says the same thing
about the URL.

CALENDAR MONTH, not billing anniversary. Conecto's period key is a month
string, and two customers whose "September" covered different days would
make that key mean nothing. It also matches every other usage surface in
the platform, so a customer comparing the app against the no-login link
sees one number rather than two.

THE FIGURES COME FROM UsageBucket, via radiusauth.usage.usage_report --
the same function the staff usage report draws. Two queries regardless of
how many customers, which is the only reason a nightly run over everybody
is reasonable at all. It also means attribution is correct by
construction: bytes land in the hour they were reported, rather than in
the month the session happened to start.

A CUSTOMER WITH NO TRAFFIC IS NOT PUSHED. usage_report only returns
customers with banked bytes, and that is deliberate rather than
incidental: pushing a zero would put a usage figure in front of somebody
whose service has not been measured yet, which reads as "you used nothing"
rather than "we have nothing to show".

THE RUN IS SEQUENTIAL, one HTTP call per changed customer. On a busy first
night that is one call per active customer; --limit exists for trying a
small batch first.
"""
import datetime

from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "Push month-to-date usage to Conecto (dry run unless --apply)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--period",
            help="Calendar month as YYYY-MM. Defaults to the month happening now.",
        )
        parser.add_argument(
            "--client-code", help="One customer only, e.g. SMF463. Useful for a first run.",
        )
        parser.add_argument(
            "--limit", type=int, help="Stop after this many pushes.",
        )
        parser.add_argument(
            "--resend", action="store_true",
            help="Send even where the figures have not changed since the last push.",
        )
        parser.add_argument(
            "--apply", action="store_true", help="Actually send. Without it, nothing leaves.",
        )

    def handle(self, *args, **options):
        from conecto import client
        from conecto.models import ConectoUsagePush
        from customers.models import Customer
        from radiusauth.usage import usage_report

        period = options.get("period") or timezone.localdate().strftime("%Y-%m")
        try:
            anchor = datetime.datetime.strptime(period, "%Y-%m").date()
        except ValueError:
            self.stderr.write(f'--period must look like 2026-09, not "{period}".')
            return

        queryset = Customer.objects.exclude(customer_id="").exclude(customer_id__isnull=True)
        if options["client_code"]:
            code = options["client_code"].strip()
            queryset = queryset.filter(customer_id__iexact=code)

        # Materialised ONCE. usage_report needs the objects, and so does the
        # push below; re-walking the queryset per row would turn a two-query
        # job into one query per customer, which is the exact mistake
        # usage_report's own docstring warns about.
        customers = list(queryset)
        if not customers:
            self.stderr.write(
                f'No customer with client code "{options["client_code"]}".'
                if options["client_code"] else "No customers have a client code."
            )
            return
        by_pk = {c.pk: c for c in customers}

        rows = usage_report(customers, period="month", anchor=anchor)["results"]

        # What Conecto were last told, in one query rather than one per row.
        known = {
            row.customer_id: row
            for row in ConectoUsagePush.objects.filter(
                customer_id__in=by_pk.keys(), period=period
            )
        }

        apply = options["apply"]
        limit = options["limit"]
        now = timezone.now()

        pushed = skipped = failed = 0
        for row in rows:
            if limit is not None and pushed >= limit:
                break

            digest = client.usage_digest(
                row["download_bytes"], row["upload_bytes"], row["cap_bytes"]
            )
            link = known.get(row["customer"])
            if link is not None and link.pushed_digest == digest and not options["resend"]:
                skipped += 1
                continue

            gb = (row["total_bytes"]) / 1024 ** 3
            self.stdout.write(
                f"  {row['customer_ref']:<12} {row['full_name'][:28]:<28} "
                f"{gb:>8.2f} GB"
                + ("" if row["cap_bytes"] else "   (uncapped)")
            )

            if not apply:
                pushed += 1
                continue

            customer = by_pk[row["customer"]]
            result = client.push_usage(
                customer, period,
                row["download_bytes"], row["upload_bytes"],
                cap_bytes=row["cap_bytes"], as_of=now,
            )
            if result is None:
                # The call failed, or the integration is switched off. Either
                # way the digest is NOT recorded: recording it would mean the
                # next run skipped a figure that never arrived. The failure
                # itself is a ConectoCall row that conecto_resync can replay.
                failed += 1
                continue

            ConectoUsagePush.objects.update_or_create(
                customer=customer, period=period,
                defaults={
                    "download_bytes": row["download_bytes"],
                    "upload_bytes": row["upload_bytes"],
                    "pushed_digest": digest,
                    "pushed_at": now,
                },
            )
            pushed += 1

        verb = "pushed" if apply else "would push"
        self.stdout.write(
            f"\n{period}: {len(rows)} customer(s) with traffic, "
            f"{verb} {pushed}, {skipped} unchanged since the last push"
            + (f", {failed} failed" if failed else "")
        )
        if not apply and pushed:
            self.stdout.write("Nothing was sent. Add --apply when the list above looks right.")
