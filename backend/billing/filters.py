from datetime import timedelta

import django_filters
from django.utils import timezone

from .models import Invoice


class InvoiceFilter(django_filters.FilterSet):
    """
    Adds three search modes on top of the plain status/customer filters:

    - `document_type`: quote / proforma / invoice, for the quick buttons on
      Finance -> Invoices. "invoice" means everything that ISN'T a quote or
      pro forma, which no exact-status filter can express.

    - `overdue_within_days`: quick presets for "0-30 / 0-60 / 0-90 days
      overdue" — unpaid invoices whose due date has passed, within the
      given number of days. These are cumulative ranges (0-60 includes
      everything 0-30 would), matching how the buttons read on the
      frontend, not the non-overlapping 0-30/30-60/60-90 buckets some
      billing dashboards use.
    - `date_created_from` / `date_created_to`: a custom date range over
      when the invoice was created, for anything the presets don't cover.
    """

    # Which KIND of document, as opposed to its exact status. `status=quote`
    # already worked for quotes, but there was no way to ask for "real
    # invoices only" -- that's five statuses (draft/unpaid/paid/overdue/
    # cancelled) and an exact-match status filter can't express it. This can,
    # and it keeps the frontend from having to know which statuses count as
    # pre-invoice.
    document_type = django_filters.ChoiceFilter(
        method="filter_document_type",
        choices=[("quote", "Quote"), ("proforma", "Pro forma"), ("invoice", "Invoice")],
    )
    overdue_within_days = django_filters.NumberFilter(method="filter_overdue_within_days")
    date_created_from = django_filters.DateFilter(field_name="date_created", lookup_expr="gte")
    date_created_to = django_filters.DateFilter(field_name="date_created", lookup_expr="lte")

    class Meta:
        model = Invoice
        fields = ["status", "customer"]

    def filter_document_type(self, queryset, name, value):
        if value == Invoice.Status.QUOTE:
            return queryset.filter(status=Invoice.Status.QUOTE)
        if value == Invoice.Status.PROFORMA:
            return queryset.filter(status=Invoice.Status.PROFORMA)
        if value == "invoice":
            # Everything that isn't a quote or a pro forma. Derived from
            # PRE_INVOICE_STATUSES rather than listing the five invoice
            # statuses, so adding a status later can't silently drop it out
            # of this filter.
            return queryset.exclude(status__in=Invoice.PRE_INVOICE_STATUSES)
        return queryset

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
