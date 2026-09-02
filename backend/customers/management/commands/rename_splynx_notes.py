from django.core.management.base import BaseCommand
from django.db.models import Value
from django.db.models.functions import Replace

from customers.models import Customer


class Command(BaseCommand):
    """One-time cleanup: the CSV importer used to write "Splynx ID: <id>"
    into Customer.notes for every row imported from a legacy-platform
    export (see CustomerViewSet.extra_row_validation). That label is
    visible to staff on the customer's own record, so it's a live trace of
    the old platform's name in the app, not just an internal comment.

    The importer itself has already been changed to write "Legacy ID: ..."
    going forward. This command does the matching one-time fix on
    already-imported customers: it only rewrites the label text itself
    ("Splynx ID:" -> "Legacy ID:") and leaves the actual id value, the rest
    of the notes field, and every other field on the customer completely
    untouched -- nothing about the customer's account, billing, or service
    history changes.

    Usage:
        python manage.py rename_splynx_notes            # apply the fix
        python manage.py rename_splynx_notes --dry-run   # preview only, no writes
    """

    help = "Rewrites the 'Splynx ID:' label in Customer.notes to 'Legacy ID:' (no other changes)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show how many customers would be affected without changing anything.",
        )

    def handle(self, *args, **options):
        affected = Customer.objects.filter(notes__icontains="Splynx ID:")
        count = affected.count()

        if count == 0:
            self.stdout.write(self.style.SUCCESS("No customers have 'Splynx ID:' in their notes. Nothing to do."))
            return

        if options["dry_run"]:
            self.stdout.write(f"Would update {count} customer(s):")
            for customer in affected.order_by("id")[:20]:
                self.stdout.write(f"  #{customer.id} {customer.full_name}: {customer.notes!r}")
            if count > 20:
                self.stdout.write(f"  ... and {count - 20} more")
            self.stdout.write(self.style.WARNING("Dry run only -- no changes made. Re-run without --dry-run to apply."))
            return

        updated = affected.update(notes=Replace("notes", Value("Splynx ID:"), Value("Legacy ID:")))
        self.stdout.write(self.style.SUCCESS(f"Updated {updated} customer(s): 'Splynx ID:' -> 'Legacy ID:' in notes."))
