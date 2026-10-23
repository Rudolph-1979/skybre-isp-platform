"""Link existing jobs to the client whose code is in their title.

    python manage.py link_jobs_to_clients            # dry run: list them
    python manage.py link_jobs_to_clients --apply

New and edited jobs link themselves (Job.save); this catches the jobs made
before that. Only jobs with no client yet, not deleted, and not finished
are touched. Saving a linked job re-sends it to the technician's Conecto
app with the client's name, phone and address.
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Link jobs with a client code in the title to that client (dry run unless --apply)."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Actually link them.")
        parser.add_argument("--all", action="store_true", help="Include completed and cancelled jobs.")

    def handle(self, *args, **options):
        from scheduling.client_link import client_from_title
        from scheduling.models import Job

        jobs = Job.objects.filter(customer__isnull=True, deleted_at__isnull=True)
        if not options["all"]:
            jobs = jobs.exclude(status__in=[Job.Status.COMPLETED, Job.Status.CANCELLED])
        linked = unmatched = 0
        for job in jobs.order_by("pk"):
            customer = client_from_title(job.title)
            if customer is None:
                unmatched += 1
                continue
            linked += 1
            self.stdout.write(f"  job {job.pk:<6} {job.title[:45]:<45} -> {customer.customer_id} {customer.full_name}")
            if options["apply"]:
                job.save()  # Job.save links it and the save re-sends it to Conecto
        verb = "Linked" if options["apply"] else "Would link"
        self.stdout.write(f"{verb} {linked}; {unmatched} without a client code we recognise.")
        if not options["apply"] and linked:
            self.stdout.write("Nothing was changed. Add --apply when the list above looks right.")
