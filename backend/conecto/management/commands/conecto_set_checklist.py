"""Set a technician's stock tick-list for a day.

Conecto shows this before their first job and will not let that job start
until it is submitted -- so a technician with no list set is a technician
who cannot start work. That is the failure mode worth knowing about
before using this.

Calling it again for the same technician and date REPLACES the list on
Conecto's side, so this always sends the whole thing; there is no way to
add one item.

Usage:
    python manage.py conecto_set_checklist christo "Router (dual-band)" "Fibre patch cables x4"
    python manage.py conecto_set_checklist christo --date 2026-09-17 --from-yesterday
    python manage.py conecto_set_checklist --all "Router (dual-band)" "Ladder"
"""
import datetime

from django.core.management.base import BaseCommand
from django.utils import timezone

from conecto import client
from conecto.models import ConectoStockChecklist, ConectoTechnician


class Command(BaseCommand):
    help = "Set the daily stock checklist a technician sees in Conecto."

    def add_arguments(self, parser):
        parser.add_argument("username", nargs="?", help="Staff username. Omit with --all.")
        parser.add_argument("items", nargs="*", help="The items, each quoted.")
        parser.add_argument("--date", default="", metavar="YYYY-MM-DD", help="Default: today.")
        parser.add_argument(
            "--all", action="store_true",
            help="Every active linked technician gets the same list.",
        )
        parser.add_argument(
            "--from-yesterday", action="store_true",
            help="Reuse the list this technician had on their previous set day.",
        )
        parser.add_argument("--apply", action="store_true", help="Actually send it.")

    def handle(self, *args, **options):
        if options["date"]:
            try:
                effective = datetime.date.fromisoformat(options["date"])
            except ValueError:
                self.stderr.write(self.style.ERROR("--date must be YYYY-MM-DD."))
                return
        else:
            effective = timezone.localdate()

        links = ConectoTechnician.objects.filter(is_active=True).select_related("user")
        if not options["all"]:
            if not options["username"]:
                self.stderr.write(self.style.ERROR("Give a username, or --all."))
                return
            links = links.filter(user__username=options["username"])
            if not links:
                self.stderr.write(self.style.ERROR(
                    f'No active Conecto technician for "{options["username"]}". '
                    "Link them with conecto_link_technician first."
                ))
                return

        for link in links:
            items = list(options["items"])
            if options["from_yesterday"]:
                previous = (
                    ConectoStockChecklist.objects.filter(technician_code=link.technician_code)
                    .exclude(effective_date=effective)
                    .order_by("-effective_date")
                    .first()
                )
                if previous is None:
                    self.stderr.write(self.style.WARNING(
                        f"{link.technician_code}: no earlier list to copy; skipped."
                    ))
                    continue
                items = list(previous.items)

            if not items:
                self.stderr.write(self.style.ERROR(
                    f"{link.technician_code}: no items given. An empty list would clear theirs."
                ))
                continue

            self.stdout.write(f"{link.technician_code} ({link.user.username}) — {effective}:")
            for item in items:
                self.stdout.write(f"    · {item}")

            if not options["apply"]:
                continue

            sent = client.set_stock_checklist(link.technician_code, effective, items)
            ConectoStockChecklist.objects.update_or_create(
                technician_code=link.technician_code,
                effective_date=effective,
                defaults={"items": items, "sent_at": timezone.now() if sent else None},
            )
            if sent is None:
                self.stderr.write(self.style.WARNING(
                    f"  {link.technician_code}: send failed — see the Conecto calls table, "
                    "then `conecto_resync --apply`. The list is recorded here either way."
                ))
            else:
                self.stdout.write(self.style.SUCCESS(f"  {link.technician_code}: sent."))

        if not options["apply"]:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING(
                "Dry run -- nothing sent. Re-run with --apply."
            ))
