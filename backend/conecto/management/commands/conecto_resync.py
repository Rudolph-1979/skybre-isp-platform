"""Replay outbound calls that failed.

Conecto has no retry queue -- their spec says a failed webhook is logged
and dropped -- and nothing in our own request cycle may block on their
Cloud Run functions. So every failure lands in a ConectoCall row and
this command is what drains them.

It re-sends the PAYLOAD AS IT WAS, not a payload rebuilt from the record
as it stands now. A ticket edited since the failed push should be
re-sent as the event that failed; rebuilding it would quietly turn a
retry into a different call, and the createTicket that failed yesterday
would arrive today carrying today's status.

Successful replays are marked ok and their attempt count raised, so the
table stays a history rather than a queue that empties into nothing.

Usage:
    python manage.py conecto_resync                 # report only
    python manage.py conecto_resync --apply
    python manage.py conecto_resync --apply --since 24    # last 24 hours
    python manage.py conecto_resync --apply --endpoint createTicket
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from conecto import client
from conecto.models import ConectoCall, ConectoTicket
from conecto.signals import _extract_ticket_id


class Command(BaseCommand):
    help = "Re-send Conecto calls that failed."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true",
                            help="Actually re-send. Without this the command only reports.")
        parser.add_argument("--since", type=int, default=0, metavar="HOURS",
                            help="Only failures from the last N hours. Default: all.")
        parser.add_argument("--endpoint", default="",
                            help="Only this endpoint, e.g. createTicket.")
        parser.add_argument("--limit", type=int, default=200,
                            help="Most calls to replay in one run. Default 200.")

    def handle(self, *args, **options):
        failures = ConectoCall.objects.filter(status=ConectoCall.Status.FAILED)
        if options["since"]:
            failures = failures.filter(
                created_at__gte=timezone.now() - timedelta(hours=options["since"])
            )
        if options["endpoint"]:
            failures = failures.filter(endpoint=options["endpoint"])
        else:
            # A notification is news: "suspended in 2 days" replayed a week
            # later is wrong. Only replay ones from the last 24 hours unless
            # asked for by name (12 Oct 2026).
            failures = failures.exclude(
                endpoint="customerNotification",
                created_at__lt=timezone.now() - timedelta(hours=24),
            )
        failures = failures.order_by("created_at")[: options["limit"]]

        rows = list(failures)
        if not rows:
            self.stdout.write("No failed calls to replay.")
            return

        self.stdout.write(f"{len(rows)} failed call(s):")
        for row in rows:
            self.stdout.write(
                f"  {row.created_at:%Y-%m-%d %H:%M}  {row.endpoint:<18} "
                f"{row.subject_kind}:{row.subject_ref or '-'}  "
                f"({row.http_status or 'no response'}) {row.detail[:70]}"
            )

        if not options["apply"]:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING(
                "Dry run -- nothing sent. Re-run with --apply to replay these."
            ))
            return

        sent, still_failing = 0, 0
        for row in rows:
            # existing=row: this updates the failed row in place instead of
            # writing a second one beside it, so the table stays a list of
            # what still needs fixing rather than a list of every attempt.
            response = client.call(
                row.endpoint,
                row.payload,
                url=self._url_for(row),
                method="patch" if row.endpoint == "updateTicket" else "post",
                subject_kind=row.subject_kind,
                subject_ref=row.subject_ref,
                existing=row,
            )
            if response is None:
                still_failing += 1
                continue

            sent += 1

            # A createTicket that finally landed carries the id every later
            # update needs. Without storing it here the replay would look
            # like it worked and the ticket would still be unupdatable.
            if row.endpoint == "createTicket":
                self._link_ticket(row, response)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Replayed {sent}."))
        if still_failing:
            self.stdout.write(self.style.WARNING(
                f"{still_failing} still failing -- their rows carry the latest reason "
                "and attempt count."
            ))

    def _url_for(self, row):
        """updateTicket puts the Conecto id in the URL path, so the stored
        payload alone is not enough to rebuild the call."""
        if row.endpoint != "updateTicket":
            return None
        link = ConectoTicket.objects.filter(ticket__ticket_number=row.subject_ref).first()
        if link is None:
            return None
        base = client._url("updateTicket").rstrip("/")
        return f"{base}/{link.conecto_ticket_id}"

    def _link_ticket(self, row, response):
        from tickets.models import Ticket

        conecto_id = _extract_ticket_id(response)
        if not conecto_id:
            return
        ticket = Ticket.objects.filter(ticket_number=row.subject_ref).first()
        if ticket is None:
            return
        ConectoTicket.objects.get_or_create(
            ticket=ticket,
            defaults={
                "conecto_ticket_id": conecto_id,
                "origin": ConectoTicket.Origin.SKYBRE,
            },
        )
