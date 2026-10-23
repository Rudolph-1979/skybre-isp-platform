import django_filters
from .models import Job, Shift


class JobFilter(django_filters.FilterSet):
    """`start_from`/`start_to` let the calendar UI fetch just the jobs
    whose start time falls within the currently visible date range,
    rather than every job ever created. A pool job's start is NULL, so
    these two already exclude it without any extra condition -- NULL
    never satisfies `>=`/`<=` in SQL.

    `unscheduled=1` is the pool panel's own fetch: the exact complement,
    every job whose start (and therefore end) is blank."""

    start_from = django_filters.IsoDateTimeFilter(field_name="start", lookup_expr="gte")
    start_to = django_filters.IsoDateTimeFilter(field_name="start", lookup_expr="lte")
    unscheduled = django_filters.BooleanFilter(field_name="start", lookup_expr="isnull")

    class Meta:
        model = Job
        fields = ["status", "job_type", "assigned_technicians", "customer", "ticket", "start_from", "start_to", "unscheduled"]


class ShiftFilter(django_filters.FilterSet):
    start_from = django_filters.IsoDateTimeFilter(field_name="start", lookup_expr="gte")
    start_to = django_filters.IsoDateTimeFilter(field_name="start", lookup_expr="lte")

    class Meta:
        model = Shift
        fields = ["status", "staff", "start_from", "start_to"]
