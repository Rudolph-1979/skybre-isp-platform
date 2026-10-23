"""Copy any job-card signatures that didn't come through when the card
arrived (Conecto's link was slow or down). Safe to run any time.

    python manage.py conecto_fetch_job_card_signatures
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Retry copying job-card signature images into the database."

    def handle(self, *args, **options):
        from conecto.job_cards import fetch_signature
        from conecto.models import ConectoJobCard

        pending = ConectoJobCard.objects.filter(signature_png__isnull=True).exclude(signature_url="")
        ok = failed = 0
        for pk in pending.values_list("pk", flat=True):
            if fetch_signature(pk):
                ok += 1
            else:
                failed += 1
        self.stdout.write(f"Copied {ok}; still failing {failed}.")
