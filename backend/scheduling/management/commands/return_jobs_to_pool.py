"""Send jobs nobody completed back to the job pool, once a day.

Run it every few minutes; it only acts once a day, at or after the time
set under Scheduling -> Pool settings (SchedulingSettings), so changing
that time needs no crontab edit:

    */5 * * * * cd /home/ubuntu/isp-platform && docker compose exec -T backend \\
        python manage.py return_jobs_to_pool >/dev/null 2>&1

--now runs it immediately whatever the time (and still counts as today's).
"""
from django.core.management.base import BaseCommand

from scheduling.pool import run_daily_return


class Command(BaseCommand):
    help = "Return uncompleted jobs whose time has passed to the job pool (daily, at the set time)."

    def add_arguments(self, parser):
        parser.add_argument("--now", action="store_true", help="Run now, whatever the set time.")

    def handle(self, *args, **options):
        from audit.context import acting_as_system

        # Named in the Scheduling changelog (8 Oct 2026).
        with acting_as_system("Daily return to pool"):
            moved = run_daily_return(force=options["now"])
        if moved is None:
            self.stdout.write("Not due (switched off, before the set time, or already done today).")
        else:
            self.stdout.write(f"{moved} uncompleted job(s) returned to the pool.")
