"""Assign tariffs to partners from their names.

Tariffs on this platform are named after the partner that sells them --
"MTD 50 Mbps", "Skybre Wireless 4 Mbps", "Vuma BF 30/30 Mbps" -- so the
assignment is already written down, just not in a column. This proposes
it rather than making anyone click through thirty plans.

It PROPOSES. Nothing is written without --apply, and what it cannot work
out it reports rather than guessing, because a tariff assigned to the
wrong partner silently removes it from the people who should be selling
it and offers it to people who should not.

Two kinds of match:

  * EXACT PREFIX -- the tariff name starts with a partner's name.
    "MTD 50 Mbps" -> MTD. Safe enough to propose on its own.
  * EXPLICIT RULE -- --map "Vuma=Vumatel", for names where the plan's
    prefix is not the partner's name. Nothing is inferred here; you say
    it, it does exactly that.

Anything matching neither is listed, grouped by first word, so the next
run can name the rules it needs. Tariffs that already have a partner are
left alone unless --overwrite is given.

Usage:
    python manage.py assign_tariff_partners
    python manage.py assign_tariff_partners --map "Vuma=Vumatel" --map "SCF=Sponsors & Deals"
    python manage.py assign_tariff_partners --map "Vuma=Vumatel" --apply
"""
from collections import defaultdict

from django.core.management.base import BaseCommand

from billing.models import Tariff
from customers.models import Partner


class Command(BaseCommand):
    help = "Propose (and optionally apply) a partner for each tariff, from its name."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply", action="store_true",
            help="Actually write the assignments. Without this the command only reports.",
        )
        parser.add_argument(
            "--map", action="append", default=[], metavar="PREFIX=PARTNER",
            help=(
                'Explicit rule for names the partner name does not start, e.g. '
                '--map "Vuma=Vumatel". Repeatable. Matched case-insensitively.'
            ),
        )
        parser.add_argument(
            "--overwrite", action="store_true",
            help="Also reassign tariffs that already have a partner. Off by default.",
        )

    def handle(self, *args, **options):
        partners = list(Partner.objects.all())
        if not partners:
            self.stderr.write(self.style.ERROR(
                "No partners exist yet. Add them under Configs -> Partners first."
            ))
            return

        by_name = {p.name.lower(): p for p in partners}

        # Explicit rules first, and validated up front: a typo in a partner
        # name should stop the run, not silently skip a rule and leave the
        # operator believing it was applied.
        rules = []
        for raw in options["map"]:
            if "=" not in raw:
                self.stderr.write(self.style.ERROR(f'--map needs PREFIX=PARTNER, got "{raw}".'))
                return
            prefix, _, partner_name = raw.partition("=")
            partner = by_name.get(partner_name.strip().lower())
            if partner is None:
                self.stderr.write(self.style.ERROR(
                    f'No partner named "{partner_name.strip()}". '
                    f'Known: {", ".join(sorted(p.name for p in partners))}'
                ))
                return
            rules.append((prefix.strip().lower(), partner))

        # Longest prefix first, so "Skybre Wireless" wins over "Skybre" if
        # both exist. Otherwise the answer would depend on dictionary order,
        # which is not a basis for deciding who sells a plan.
        name_rules = sorted(
            ((p.name.lower(), p) for p in partners), key=lambda r: -len(r[0])
        )
        rules = sorted(rules, key=lambda r: -len(r[0])) + name_rules

        tariffs = Tariff.objects.select_related("partner").order_by("name")
        proposed, unmatched, skipped = [], [], []

        for tariff in tariffs:
            if tariff.partner_id and not options["overwrite"]:
                skipped.append(tariff)
                continue
            lowered = tariff.name.lower()
            match = next((p for prefix, p in rules if lowered.startswith(prefix)), None)
            if match is None:
                unmatched.append(tariff)
            elif tariff.partner_id == match.pk:
                skipped.append(tariff)
            else:
                proposed.append((tariff, match))

        self.stdout.write(f"Tariffs: {tariffs.count()}")
        self.stdout.write(f"  already assigned / unchanged: {len(skipped)}")
        self.stdout.write(f"  proposed:                     {len(proposed)}")
        self.stdout.write(f"  no rule matched:              {len(unmatched)}")

        if proposed:
            self.stdout.write("")
            self.stdout.write("Proposed:")
            width = max(len(t.name) for t, _ in proposed)
            for tariff, partner in proposed:
                was = tariff.partner.name if tariff.partner_id else "—"
                self.stdout.write(f"  {tariff.name:<{width}}  {was}  ->  {partner.name}")

        if unmatched:
            # Grouped by first word: that is the shape of the rule that is
            # missing, so the output reads as a to-do list for the next run.
            self.stdout.write("")
            self.stdout.write("No rule matched (add --map for these):")
            groups = defaultdict(list)
            for tariff in unmatched:
                groups[tariff.name.split(" ")[0]].append(tariff.name)
            for first_word, names in sorted(groups.items()):
                self.stdout.write(f'  --map "{first_word}=<partner>"   ({len(names)}: {names[0]}…)')

        if not options["apply"]:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING(
                "Dry run -- nothing written. Re-run with --apply once the proposals look right."
            ))
            return

        if not proposed:
            self.stdout.write("")
            self.stdout.write("Nothing to change.")
            return

        for tariff, partner in proposed:
            tariff.partner = partner
            tariff.save(update_fields=["partner"])

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Assigned {len(proposed)} tariff(s)."))
        if unmatched:
            self.stdout.write(
                f"{len(unmatched)} still have no partner, so they stay available to everyone "
                "until you assign them."
            )
