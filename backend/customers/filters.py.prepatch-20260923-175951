import django_filters
from django.utils import timezone

from .models import Customer, CustomerTask


class CustomerFilter(django_filters.FilterSet):
    """Plain equality filters (status/category/customer_type), plus:

    - `overdue`: true = only customers with at least one invoice that's
      unpaid/overdue and past its due date. Matches the same definition of
      "overdue" used by the Invoices page's date-range filter (see
      billing/filters.py's filter_overdue_within_days) — an invoice's
      `status` field isn't reliably flipped to "overdue" the instant its
      due date passes, so this checks the due date directly rather than
      trusting the status label alone.
    - `partner_in`: comma-separated partner ids -- the Customers page's
      "which partners do I want to see" filter, so a staff member can pick
      more than one of their allowed partners at once. `partner` (plain
      equality, from Meta.fields below) still works for a single exact
      match.
    - `high_alert`: true = only customers who logged 3+ tickets in a single
      calendar month at some point in the last 6 months. This is where the
      dashboard's High alert tile now lands, and it shares that tile's
      definition (tickets.alerts) rather than restating it -- the tile's
      number and this list have to agree, or neither is worth showing.
    """

    overdue = django_filters.BooleanFilter(method="filter_overdue")
    partner_in = django_filters.CharFilter(method="filter_partner_in")
    high_alert = django_filters.BooleanFilter(method="filter_high_alert")

    class Meta:
        model = Customer
        fields = ["status", "category", "customer_type", "partner"]

    def filter_overdue(self, queryset, name, value):
        if not value:
            return queryset
        today = timezone.localdate()
        return queryset.filter(
            invoices__status__in=["unpaid", "overdue"],
            invoices__date_due__lt=today,
        ).distinct()

    def filter_high_alert(self, queryset, name, value):
        if not value:
            return queryset
        from tickets.alerts import high_alert_customer_ids

        # `queryset` is already partner-scoped by CustomerViewSet.get_queryset,
        # so passing it in keeps a reseller-scoped staff member from turning up
        # customers outside their partners through this filter.
        return queryset.filter(pk__in=high_alert_customer_ids(queryset))

    def filter_partner_in(self, queryset, name, value):
        ids = [v.strip() for v in value.split(",") if v.strip().isdigit()]
        if not ids:
            return queryset
        return queryset.filter(partner_id__in=ids)


class CustomerTaskFilter(django_filters.FilterSet):
    """Plain equality filters, plus:

    - `outstanding`: true = only tasks that still need doing (Open or In
      Progress). Shares CustomerTask.OPEN_STATUSES rather than restating
      the pair, so this filter and the model can't drift apart.
    - `overdue`: true = outstanding AND past its due date. Tasks with no
      due date are never overdue, so they're excluded here rather than
      being swept in by a null comparison.
    """

    outstanding = django_filters.BooleanFilter(method="filter_outstanding")
    overdue = django_filters.BooleanFilter(method="filter_overdue")

    class Meta:
        model = CustomerTask
        fields = ["customer", "status", "priority", "assigned_to"]

    def filter_outstanding(self, queryset, name, value):
        if value is None:
            return queryset
        if value:
            return queryset.filter(status__in=CustomerTask.OPEN_STATUSES)
        return queryset.exclude(status__in=CustomerTask.OPEN_STATUSES)

    def filter_overdue(self, queryset, name, value):
        if not value:
            return queryset
        return queryset.filter(
            status__in=CustomerTask.OPEN_STATUSES,
            due_date__lt=timezone.localdate(),
        )
