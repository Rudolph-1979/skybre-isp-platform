import django_filters
from django.utils import timezone

from .models import Customer


class CustomerFilter(django_filters.FilterSet):
    """Plain equality filters (status/category/customer_type), plus:

    - `overdue`: true = only customers with at least one invoice that's
      unpaid/overdue and past its due date. Matches the same definition of
      "overdue" used by the Invoices page's date-range filter (see
      billing/filters.py's filter_overdue_within_days) — an invoice's
      `status` field isn't reliably flipped to "overdue" the instant its
      due date passes, so this checks the due date directly rather than
      trusting the status label alone.
    """

    overdue = django_filters.BooleanFilter(method="filter_overdue")

    class Meta:
        model = Customer
        fields = ["status", "category", "customer_type"]

    def filter_overdue(self, queryset, name, value):
        if not value:
            return queryset
        today = timezone.localdate()
        return queryset.filter(
            invoices__status__in=["unpaid", "overdue"],
            invoices__date_due__lt=today,
        ).distinct()
