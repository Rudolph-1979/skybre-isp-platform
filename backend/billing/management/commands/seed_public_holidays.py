"""Fill the public-holiday table with South Africa's statutory days.

    python manage.py seed_public_holidays --years 2026 2028
    python manage.py seed_public_holidays --years 2026 2028 --apply

DRY RUN BY DEFAULT. These dates decide when customers are disconnected,
so the list gets looked at before it is stored.

WHAT THIS IS AND IS NOT

It is a starting point. It is NOT the authority, and it cannot be:

  * one-off holidays get DECLARED -- election days, days of mourning,
    typically at a few weeks' notice. Nothing in this file can know about
    them. Add them by hand in the admin; a hand-added row is never touched
    by a re-seed.
  * your own closure days -- a December shutdown, a staff day -- belong in
    the same table and are equally none of this command's business.
  * this list was written from knowledge, not fetched from a government
    source. Read it before applying it.

THE SUNDAY RULE. The Public Holidays Act makes the following Monday a
public holiday whenever one falls on a Sunday. That is applied here, and
it is the part most worth checking in the dry run, because it is the part
a hand-maintained list always forgets.

MOVEABLE DAYS. Good Friday and Family Day hang off Easter, which is
computed with the anonymous Gregorian algorithm rather than tabulated --
a table would quietly run out and start seeding nothing.
"""
import datetime

from django.core.management.base import BaseCommand

#: (month, day, name) for the days that do not move.
FIXED = [
    (1, 1, "New Year's Day"),
    (3, 21, "Human Rights Day"),
    (4, 27, "Freedom Day"),
    (5, 1, "Workers' Day"),
    (6, 16, "Youth Day"),
    (8, 9, "National Women's Day"),
    (9, 24, "Heritage Day"),
    (12, 16, "Day of Reconciliation"),
    (12, 25, "Christmas Day"),
    (12, 26, "Day of Goodwill"),
]


def easter_sunday(year):
    """Easter Sunday, by the anonymous Gregorian algorithm.

    Computed rather than tabulated: a table of dates is a thing that works
    until the year it runs out, and then seeds an empty list without
    complaining.
    """
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month, day = divmod(h + m - 7 * n + 114, 31)
    return datetime.date(year, month, day + 1)


def holidays_for(year):
    """Every statutory public holiday in one year, Sunday rule applied."""
    days = [(datetime.date(year, month, day), name) for month, day, name in FIXED]

    easter = easter_sunday(year)
    days.append((easter - datetime.timedelta(days=2), "Good Friday"))
    days.append((easter + datetime.timedelta(days=1), "Family Day"))

    # The Public Holidays Act: a holiday falling on a Sunday makes the
    # following Monday one too. Collected separately and added afterwards so
    # a Monday that is already a holiday in its own right is not duplicated
    # or renamed.
    observed = [
        (date + datetime.timedelta(days=1), f"{name} observed")
        for date, name in days
        if date.weekday() == 6
    ]
    taken = {date for date, _ in days}
    days.extend((date, name) for date, name in observed if date not in taken)

    return sorted(days)


class Command(BaseCommand):
    help = "Seed South African public holidays (dry run unless --apply)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--years", nargs=2, type=int, metavar=("FROM", "TO"),
            help="Inclusive year range, e.g. --years 2026 2028. Defaults to this year and the next two.",
        )
        parser.add_argument(
            "--apply", action="store_true", help="Actually write. Without it, nothing is stored.",
        )

    def handle(self, *args, **options):
        from django.utils import timezone

        from billing.models import PublicHoliday

        if options["years"]:
            first, last = options["years"]
        else:
            first = timezone.localdate().year
            last = first + 2
        if last < first:
            self.stderr.write("--years wants the earlier year first.")
            return

        existing = {
            row.date: row
            for row in PublicHoliday.objects.filter(
                date__gte=datetime.date(first, 1, 1), date__lte=datetime.date(last, 12, 31)
            )
        }

        written = kept_by_hand = unchanged = 0
        for year in range(first, last + 1):
            self.stdout.write(f"\n{year}")
            for date, name in holidays_for(year):
                row = existing.get(date)
                if row is not None and not row.seeded:
                    # Somebody typed this one in. It wins: they knew
                    # something this command does not.
                    kept_by_hand += 1
                    self.stdout.write(
                        f"  {date}  {name:<28} -- kept as entered by hand: {row.name!r}"
                    )
                    continue
                if row is not None and row.name == name:
                    unchanged += 1
                    continue

                self.stdout.write(f"  {date}  {name}")
                written += 1
                if options["apply"]:
                    PublicHoliday.objects.update_or_create(
                        date=date, defaults={"name": name, "seeded": True},
                    )

        verb = "wrote" if options["apply"] else "would write"
        line = f"\n{first}-{last}: {verb} {written}"
        if unchanged:
            line += f", {unchanged} already correct"
        if kept_by_hand:
            line += f", {kept_by_hand} left as entered by hand"
        self.stdout.write(line)
        if not options["apply"] and written:
            self.stdout.write(
                "Nothing was stored. Read the list above -- it decides when customers "
                "are disconnected -- then add --apply."
            )
        self.stdout.write(
            "Declared one-off holidays and your own closure days are not in this list. "
            "Add them under Public holidays in the admin; a re-seed leaves them alone."
        )
