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
    - `line_suspended`: `any` = customers with at least one Suspended
      service, whatever the reason; `nonpayment` = only those with a line
      the billing run suspended for non-payment. Their own status is not
      looked at -- billing leaves it as it was. The dashboard's "Suspended
      for non-payment" tile counts through the same definition
      (customers/suspension.py suspended_services).
    - `payment_status`: owing / overdue / overdue_partial / partial / unpaid
      / paid / credit -- the Payment status column (customers.payment_status).
    """

    overdue = django_filters.BooleanFilter(method="filter_overdue")
    partner_in = django_filters.CharFilter(method="filter_partner_in")
    high_alert = django_filters.BooleanFilter(method="filter_high_alert")
    line_suspended = django_filters.CharFilter(method="filter_line_suspended")
    payment_status = django_filters.CharFilter(method="filter_payment_status")
    # Several statuses at once (Rudolph, 6 Oct 2026: "Make the options one or
    # more selectable"): comma-separated, matching ANY of them. Takes the
    # Customer statuses plus the two "Line suspended" options, which are
    # about the customer's lines rather than Customer.status.
    status_any = django_filters.CharFilter(method="filter_status_any")
    # Digital contracts (6 Oct 2026): signed / awaiting / none.
    contract = django_filters.CharFilter(method="filter_contract")
    # Payment method on the client's billing config (7 Oct 2026, replaces
    # the Type dropdown on the Customers page): a PaymentMethod id, or
    # "none" for clients without one.
    payment_method = django_filters.CharFilter(method="filter_payment_method")
    # How the client pays (21 Oct 2026): early / on_time / late / chronic /
    # new, comma-separated -- billing.payer_rating.
    payer = django_filters.CharFilter(method="filter_payer")

    class Meta:
        model = Customer
        fields = ["status", "category", "customer_type", "partner", "account_type"]

    def filter_overdue(self, queryset, name, value):
        if not value:
            return queryset
        today = timezone.localdate()
        return queryset.filter(
            invoices__status__in=["unpaid", "overdue"],
            invoices__date_due__lt=today,
        ).distinct()

    def filter_payer(self, queryset, name, value):
        from billing.payer_rating import ratings

        wanted = {v.strip() for v in (value or "").split(",") if v.strip()}
        if not wanted:
            return queryset
        ids = list(queryset.values_list("pk", flat=True))
        rated = ratings(ids)
        return queryset.filter(pk__in=[cid for cid, r in rated.items() if r["code"] in wanted])

    def filter_high_alert(self, queryset, name, value):
        if not value:
            return queryset
        from tickets.alerts import high_alert_customer_ids

        # `queryset` is already partner-scoped by CustomerViewSet.get_queryset,
        # so passing it in keeps a reseller-scoped staff member from turning up
        # customers outside their partners through this filter.
        return queryset.filter(pk__in=high_alert_customer_ids(queryset))

    def filter_line_suspended(self, queryset, name, value):
        value = (value or "").strip().lower()
        if value in ("any", "true", "1"):
            nonpayment_only = False
        elif value == "nonpayment":
            nonpayment_only = True
        else:
            return queryset
        from .suspension import suspended_services

        # An IN on customer ids rather than a join, so no .distinct() and
        # the page's ordering is left alone.
        return queryset.filter(
            pk__in=suspended_services(nonpayment_only).values("customer_id")
        )

    def filter_status_any(self, queryset, name, value):
        from django.db.models import Q
        from .suspension import suspended_services

        wanted = {v.strip() for v in (value or "").split(",") if v.strip()}
        if not wanted:
            return queryset
        statuses = wanted & set(Customer.Status.values)
        condition = Q(status__in=statuses) if statuses else Q(pk__in=[])
        if "line_suspended" in wanted:
            condition |= Q(pk__in=suspended_services(False).values("customer_id"))
        elif "line_suspended_nonpayment" in wanted:
            condition |= Q(pk__in=suspended_services(True).values("customer_id"))
        return queryset.filter(condition)

    def filter_payment_method(self, queryset, name, value):
        if value == "none":
            return queryset.filter(billing_config__payment_method__isnull=True)
        if str(value).isdigit():
            return queryset.filter(billing_config__payment_method_id=int(value))
        return queryset

    def filter_contract(self, queryset, name, value):
        from django.db.models import Exists, OuterRef

        from contracts.models import Contract

        signed = Exists(Contract.objects.filter(customer=OuterRef("pk"), status=Contract.Status.SIGNED))
        waiting = Exists(Contract.objects.filter(customer=OuterRef("pk"), status__in=Contract.OPEN_STATUSES))
        if value == "signed":
            return queryset.filter(signed)
        if value == "awaiting":
            return queryset.filter(waiting).exclude(signed)
        if value == "none":
            return queryset.exclude(signed).exclude(waiting)
        return queryset

    def filter_payment_status(self, queryset, name, value):
        from .payment_filter import filter_payment_status

        return filter_payment_status(queryset, value)

    def filter_partner_in(self, queryset, name, value):
        ids = [v.strip() for v in value.split(",") if v.strip().isdigit()]
        if not ids:
            return queryset
        # A client also counts for a partner one of its lines is with
        # (billing.Service.partner).
        from django.db.models import Q
        from billing.models import Service

        with_line = Service.objects.filter(partner_id__in=ids).exclude(
            status=Service.Status.TERMINATED
        ).values("customer_id")
        return queryset.filter(Q(partner_id__in=ids) | Q(pk__in=with_line))


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
