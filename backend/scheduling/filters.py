import django_filters
from .models import Job, Shift


class JobFilter(django_filters.FilterSet):
    """`start_from`/`start_to` let the calendar UI fetch just the jobs
    whose start time falls within the currently visible date range,
    rather than every job ever created."""

    start_from = django_filters.IsoDateTimeFilter(field_name="start", lookup_expr="gte")
    start_to = django_filters.IsoDateTimeFilter(field_name="start", lookup_expr="lte")

    class Meta:
        model = Job
        fields = ["status", "job_type", "assigned_to", "customer", "start_from", "start_to"]


class ShiftFilter(django_filters.FilterSet):
    start_from = django_filters.IsoDateTimeFilter(field_name="start", lookup_expr="gte")
    start_to = django_filters.IsoDateTimeFilter(field_name="start", lookup_expr="lte")

    class Meta:
        model = Shift
        fields = ["status", "staff", "start_from", "start_to"]
