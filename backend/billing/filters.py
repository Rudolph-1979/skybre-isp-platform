from datetime import timedelta

import django_filters
from django.utils import timezone

from .models import Invoice


class InvoiceFilter(django_filters.FilterSet):
    """
    Adds two search modes on top of the plain status/customer filters:

    - `overdue_within_days`: quick presets for "0-30 / 0-60 / 0-90 days
      overdue" — unpaid invoices whose due date has passed, within the
      given number of days. These are cumulative ranges (0-60 includes
      everything 0-30 would), matching how the buttons read on the
      frontend, not the non-overlapping 0-30/30-60/60-90 buckets some
      billing dashboards use.
    - `date_created_from` / `date_created_to`: a custom date range over
      when the invoice was created, for anything the presets don't cover.
    """

    overdue_within_days = django_filters.NumberFilter(method="filter_overdue_within_days")
    date_created_from = django_filters.DateFilter(field_name="date_created", lookup_expr="gte")
    date_created_to = django_filters.DateFilter(field_name="date_created", lookup_expr="lte")

    class Meta:
        model = Invoice
        fields = ["status", "customer"]

    def filter_overdue_within_days(self, queryset, name, value):
        try:
            days = int(value)
        except (TypeError, ValueError):
            return queryset
        today = timezone.localdate()
        cutoff = today - timedelta(days=days)
        return queryset.filter(
            status__in=[Invoice.Status.UNPAID, Invoice.Status.OVERDUE],
            date_due__lt=today,
            date_due__gte=cutoff,
        )
