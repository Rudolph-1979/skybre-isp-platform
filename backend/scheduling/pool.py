"""Sending a scheduled job back to the job pool.

Two ways in (Rudolph, 6 Oct 2026):

* a technician presses Return instead of Complete in the Conecto app (the
  job-status webhook with status "returned"), and
* the daily sweep at the time set under Scheduling -> Pool settings, for
  every job whose time has passed without being completed.

Either way the job loses its time and its technicians -- which withdraws it
from their phones through the usual Conecto reconcile -- goes back to
Scheduled, and carries a note of why it came back, shown on its pool card.
"""
import datetime

from django.db import transaction
from django.utils import timezone

from .models import Job, SchedulingSettings

#: Jobs in these states are finished business and never come back.
DONE_STATUSES = (Job.Status.COMPLETED, Job.Status.CANCELLED)


def return_to_pool(job, reason, kind="unscheduled"):
    """Back to the pool with `reason` on it. Returns False if it can't go
    (finished, deleted, or already in the pool). `kind` is what Conecto's
    pool notification says: "returned" when a technician pressed Return to
    Pool, "unscheduled" when it was taken off the schedule (the daily sweep)."""
    if job.deleted_at is not None or job.status in DONE_STATUSES or job.start is None:
        return False
    with transaction.atomic():
        job.assigned_technicians.clear()
        job.start = None
        job.end = None
        job.status = Job.Status.SCHEDULED
        job.returned_at = timezone.now()
        job.return_reason = (reason or "")[:255]
        job.return_count = (job.return_count or 0) + 1
        job._pool_reason = kind
        job.save(update_fields=[
            "start", "end", "status", "returned_at", "return_reason", "return_count", "updated_at",
        ])
    return True


def overdue_jobs(now=None):
    """Booked jobs whose time is over and that nobody completed."""
    now = now or timezone.now()
    return Job.objects.filter(
        deleted_at__isnull=True,
        start__isnull=False,
        end__lte=now,
        status__in=(Job.Status.SCHEDULED, Job.Status.IN_PROGRESS),
    )


def run_daily_return(now=None, force=False):
    """Once a day, at or after the set time. Returns how many jobs moved,
    or None when it wasn't due."""
    now = now or timezone.now()
    settings_ = SchedulingSettings.load()
    local = timezone.localtime(now)
    if not force:
        if not settings_.auto_return_enabled:
            return None
        if local.time() < settings_.auto_return_time:
            return None
        if settings_.last_auto_return_on == local.date():
            return None
    label = local.strftime("%d %b %Y")
    moved = 0
    for job in overdue_jobs(now).prefetch_related("assigned_technicians"):
        who = ", ".join(
            (u.first_name or u.username) for u in job.assigned_technicians.all()
        )
        when = timezone.localtime(job.start).strftime("%d %b %H:%M")
        reason = f"Not completed ({when}{' · ' + who if who else ''}) — returned automatically on {label}"
        if return_to_pool(job, reason):
            moved += 1
    settings_.last_auto_return_on = local.date()
    settings_.save(update_fields=["last_auto_return_on"])
    return moved
