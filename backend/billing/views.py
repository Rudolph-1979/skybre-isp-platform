from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.exceptions import MethodNotAllowed, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
    Tariff, Service, Invoice, Payment, CreditRequest, InvoiceDeletionRequest,
    PaymentMethod, BillingDefaults, ReminderSettings, SuspensionSettings, CustomerBillingConfig,
    RecurringBillingRun,
)
from .serializers import (
    TariffSerializer, ServiceSerializer, InvoiceSerializer,
    InvoiceCreateSerializer, PaymentSerializer, CreditRequestSerializer,
    InvoiceDeletionRequestSerializer,
    PaymentMethodSerializer, BillingDefaultsSerializer, ReminderSettingsSerializer,
    SuspensionSettingsSerializer, CustomerBillingConfigSerializer, RecurringBillingRunSerializer,
)
from accounts.permissions import (
    IsAccounts, IsAccountsOrManagement, IsAdmin, IsManagement, IsStaffMember, section_permission,
)
from config.csv_import import CSVImportMixin
from network import mikrotik
from .filters import InvoiceFilter

HasServicesAccess = section_permission("services")
HasFinanceAccess = section_permission("finance")
HasConfigsAccess = section_permission("configs")


class TariffViewSet(CSVImportMixin, viewsets.ModelViewSet):
    serializer_class = TariffSerializer
    # Counted in SQL rather than per row: the tariff list shows how many
    # services each plan carries, and doing that with .services.count() would
    # be one extra query per tariff.
    # order_by is explicit because annotate() drops the model's Meta ordering,
    # and an unordered queryset makes pagination non-deterministic.
    queryset = Tariff.objects.annotate(
        service_count=Count("services", distinct=True),
        active_service_count=Count(
            "services", filter=Q(services__status=Service.Status.ACTIVE), distinct=True
        ),
    ).order_by("name")
    filterset_fields = ["service_type", "is_active"]
    search_fields = ["name"]
    ordering_fields = [
        "name", "service_type", "price", "billing_period",
        "speed_download_kbps", "tax_rate_pct", "is_active", "created_at",
    ]

    def destroy(self, request, *args, **kwargs):
        """Delete a tariff, but only when nothing depends on it.

        Service.tariff is PROTECT, so without this the delete surfaces as a
        raw ProtectedError -- a 500, with no indication of which customers are
        in the way. And Service.pending_tariff is SET_NULL, which is worse
        than an error: deleting a tariff somebody has a change booked onto
        would silently null the tariff and leave the DATE behind, producing
        exactly the half-a-booking state the serializer refuses to let staff
        create.

        Deactivating is usually what's wanted anyway -- it takes the plan off
        the new-service dropdown while leaving every invoice that references
        it intact -- so the refusal says so.
        """
        tariff = self.get_object()

        in_use = tariff.services.count()
        booked = tariff.pending_services.count()
        if in_use or booked:
            parts = []
            if in_use:
                parts.append(f"{in_use} service{'s are' if in_use != 1 else ' is'} on it")
            if booked:
                parts.append(f"{booked} service{'s have' if booked != 1 else ' has'} a change booked onto it")
            return Response(
                {
                    "detail": (
                        f"Can't delete '{tariff.name}' — {' and '.join(parts)}. "
                        "Move those services to another plan first, or untick Active to take it "
                        "off the new-service list while leaving existing services and invoices alone."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return super().destroy(request, *args, **kwargs)

    import_model = Tariff
    import_fields = {
        "name": {"required": True},
        "service_type": {
            "default": Tariff.ServiceType.INTERNET,
            "choices": Tariff.ServiceType.values,
        },
        "price": {"required": True, "type": "decimal"},
        "billing_period": {
            "default": Tariff.BillingPeriod.MONTHLY,
            "choices": Tariff.BillingPeriod.values,
        },
        "speed_download_kbps": {"type": "int", "default": None},
        "speed_upload_kbps": {"type": "int", "default": None},
        "data_cap_gb": {"type": "int", "default": None},
        "tax_rate_pct": {"type": "decimal", "default": Decimal("0")},
        "is_active": {"type": "bool", "default": True},
        "description": {"default": ""},
    }

    def get_permissions(self):
        # list/retrieve stay open to any staff member regardless of
        # Configs access -- Finance and Services both need to browse
        # tariffs to build invoice/quote line items and service plans.
        # Only actually creating/editing/deleting/importing tariffs
        # requires the Configs section itself.
        if self.action in ("list", "retrieve"):
            return [permissions.IsAuthenticated()]
        return [permissions.IsAuthenticated(), IsStaffMember(), HasConfigsAccess()]


class ScopedByCustomerMixin:
    """Customers see only their own records; staff see only the partners
    they are allowed to.

    The partner half used to be missing here, and only here. Every other
    app applies it -- customers, sales, network, radiusauth, audit,
    bankfeeds, and UpcomingBlocksView further down this very file, whose
    docstring spells out that a reseller-scoped staff member "must not
    learn the names of customers outside their partners".

    So a staff member restricted to one reseller correctly saw only that
    reseller's customers on the Customers page, and then every partner's
    invoices, payments and services on the Finance page -- with customer
    names and totals, and, because get_object() uses the same queryset, a
    working PATCH and DELETE on a competitor's invoices.

    `customer_path` is the lookup from `model` to customers.Customer, so
    the same rule can be expressed for a model related to a customer
    directly or through another hop.
    """

    def get_base_queryset(self, model, related_name="customer", customer_path=None):
        from customers.views import scope_customers_to_user
        from customers.models import Customer

        user = self.request.user
        qs = model.objects.all()
        if user.is_staff_member:
            allowed = getattr(user, "allowed_partners", None) or []
            if not allowed or user.role == user.Role.ADMIN:
                return qs
            visible = scope_customers_to_user(Customer.objects.all(), user)
            return qs.filter(**{f"{customer_path or related_name}__in": visible})
        customer_profile = getattr(user, "customer_profile", None)
        if customer_profile is None:
            return qs.none()
        return qs.filter(**{related_name: customer_profile})


class ServiceViewSet(ScopedByCustomerMixin, viewsets.ModelViewSet):
    serializer_class = ServiceSerializer
    permission_classes = [permissions.IsAuthenticated]
    filterset_fields = ["status", "customer"]
    # DRF's OrderingFilter only accepts real queryset lookups as values for
    # ?ordering= (a (field, label) tuple's second item is just a display
    # label, NOT an alias) — so these are Django's actual related-field
    # lookup paths, and the frontend must send exactly these strings.
    ordering_fields = [
        "customer__full_name",
        "tariff__name",
        "tariff__price",
        "status",
        "start_date",
        "end_date",
        "created_at",
    ]

    def get_queryset(self):
        from django.db.models import Prefetch

        from radiusauth.models import RadiusAction

        return (
            self.get_base_queryset(Service)
            .select_related("tariff", "customer")
            # Only the newest enforcement attempt is shown per service, but a
            # Prefetch queryset can't be sliced, so the ordering is set here
            # and the serializer takes the first. Without this it is one query
            # per service on a page whose whole job is listing them.
            .prefetch_related(
                Prefetch(
                    "radius_actions",
                    queryset=RadiusAction.objects.order_by("-created_at"),
                    to_attr="recent_radius_actions",
                )
            )
        )

    def get_permissions(self):
        # HasServicesAccess passes straight through for customers (section
        # restrictions are staff-only) so this only narrows list/retrieve
        # for staff who lack the Services section, without touching what
        # customers can see of their own services in the portal.
        if self.action in ("list", "retrieve"):
            return [permissions.IsAuthenticated(), HasServicesAccess()]
        if self.action == "live_bandwidth":
            return [permissions.IsAuthenticated(), HasServicesAccess()]
        return [permissions.IsAuthenticated(), IsStaffMember(), HasServicesAccess()]

    @action(detail=True, methods=["get"], url_path="live-bandwidth")
    def live_bandwidth(self, request, pk=None):
        """Live download/upload speed for this one service, read from its
        managed Simple Queue on its device (see network.mikrotik's
        get_service_live_bandwidth). Meant to be polled by a customer's
        Service/Customer detail page ONLY while a staff member actually has
        it open -- the frontend is responsible for stopping the polling
        interval on unmount so this stops being called the moment nobody's
        watching. A per-device lock (network.mikrotik.get_device_lock)
        makes sure concurrent polls for the same device's customers queue
        onto one RouterOS connection at a time rather than each opening
        their own."""
        service = self.get_object()
        if not service.device_id:
            return Response({"detail": "This service isn't assigned to a router."}, status=400)
        device = service.device
        if not device.api_enabled:
            return Response({"detail": "This service's router doesn't have the Mikrotik API enabled."}, status=400)

        lock = mikrotik.get_device_lock(device.id)
        with lock:
            try:
                result = mikrotik.get_service_live_bandwidth(device, service.id)
            except mikrotik.MikrotikError as exc:
                return Response({"detail": str(exc)}, status=502)

        if result is None:
            return Response(
                {"detail": "No live queue found for this service yet -- enable Shaper on its router and sync shaper queues."},
                status=404,
            )
        return Response(result)


class InvoiceViewSet(ScopedByCustomerMixin, viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    filterset_class = InvoiceFilter
    ordering_fields = [
        "number",
        "customer__full_name",
        "status",
        "date_created",
        "date_due",
        "subtotal",
        "tax_total",
        "total",
        "paid_amount",
    ]

    def get_serializer_class(self):
        if self.action == "create":
            return InvoiceCreateSerializer
        return InvoiceSerializer

    def get_queryset(self):
        return self.get_base_queryset(Invoice).prefetch_related("items").select_related("customer")

    def get_permissions(self):
        # HasFinanceAccess passes straight through for customers (section
        # restrictions are staff-only) so this only narrows list/retrieve
        # for staff who lack the Finance section, without touching what
        # customers can see of their own invoices in the portal.
        if self.action in ("list", "retrieve"):
            return [permissions.IsAuthenticated(), HasFinanceAccess()]
        return [permissions.IsAuthenticated(), IsStaffMember(), HasFinanceAccess()]

    @action(detail=True, methods=["post"], url_path="convert-to-proforma")
    def convert_to_proforma(self, request, pk=None):
        """Quote -> Pro Forma. One-directional -- see Invoice.convert_to_proforma()."""
        invoice = self.get_object()
        try:
            invoice.convert_to_proforma()
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(InvoiceSerializer(invoice, context=self.get_serializer_context()).data)

    @action(detail=True, methods=["post"], url_path="convert-to-invoice")
    def convert_to_invoice(self, request, pk=None):
        """Quote -> Invoice or Pro Forma -> Invoice -- see Invoice.convert_to_invoice()."""
        invoice = self.get_object()
        try:
            invoice.convert_to_invoice()
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(InvoiceSerializer(invoice, context=self.get_serializer_context()).data)

    def destroy(self, request, *args, **kwargs):
        # Quotes and pro forma invoices need Management approval to delete
        # -- same trust tier as deleting a Customer. The only path is
        # InvoiceDeletionRequestViewSet.approve, which only Management/
        # Admin can call. See that viewset below. Real invoices
        # (draft/unpaid/paid/overdue/cancelled) are untouched by this --
        # still directly deletable by staff with Finance access, same as
        # before this feature.
        instance = self.get_object()
        if instance.status in Invoice.PRE_INVOICE_STATUSES:
            raise MethodNotAllowed(
                "DELETE",
                detail=(
                    "Quotes and pro forma invoices can't be deleted directly -- submit a deletion request "
                    "(POST /invoice-deletion-requests/) for Management to approve."
                ),
            )
        # An invoice with money against it cannot be deleted. Payment.invoice
        # is SET_NULL, so the payment rows survive the delete with their
        # credit still applied to the balance -- while releasing the
        # invoice's debit removes the other side of it. The customer is
        # left in credit by whatever they paid, with no invoice on file to
        # explain it: exactly the phantom-credit state this whole change
        # set out to eliminate, and it flips
        # blocking_candidate_services' `balance <= minimum_balance` test
        # so the line can never be suspended again.
        #
        # Refusing is deliberate rather than unwinding the payments
        # automatically. Whether that money is a refund owed, a credit to
        # carry, or a capture against the wrong invoice is a finance
        # decision, and reversing a real receipt silently is not this
        # endpoint's call to make. Payments are now reversible on their
        # own (see Payment.reverse_ledger_effect), so the route is:
        # reverse the payments, then delete.
        paid = instance.payments.count()
        if paid:
            total_paid = instance.payments.aggregate(total=Sum("amount"))["total"] or 0
            raise ValidationError({
                "detail": (
                    f"{instance.number} has {paid} payment{'s' if paid != 1 else ''} against it "
                    f"totalling {total_paid}. Deleting it would leave that money on the customer's "
                    "account as credit with no invoice to explain it. Reverse the payment"
                    f"{'s' if paid != 1 else ''} on the Finance page first, then delete the invoice."
                )
            })

        # Take the invoice's debit back off the customer's balance before
        # the row goes. Deleting an invoice used to leave the debit behind
        # with nothing left to explain it, so the customer was chased for
        # -- and eventually suspended over -- money no invoice claimed.
        # One transaction, so the balance and the row move together.
        with transaction.atomic():
            instance.release_balance_debit()
            return super().destroy(request, *args, **kwargs)


class InvoiceDeletionRequestViewSet(viewsets.ModelViewSet):
    """The only path by which a Quote or Pro Forma Invoice actually gets
    deleted -- see InvoiceViewSet.destroy above. Any staff member with
    Finance access can submit one; only Management (or Admin) can approve
    or reject it. Mirrors customers.CustomerDeletionRequestViewSet."""

    serializer_class = InvoiceDeletionRequestSerializer
    queryset = InvoiceDeletionRequest.objects.select_related(
        "invoice", "invoice__customer", "requested_by", "decided_by"
    ).all()
    filterset_fields = ["invoice", "status"]

    def get_permissions(self):
        if self.action in ("approve", "reject"):
            return [permissions.IsAuthenticated(), IsManagement(), HasFinanceAccess()]
        return [permissions.IsAuthenticated(), IsStaffMember(), HasFinanceAccess()]

    def perform_create(self, serializer):
        serializer.save(requested_by=self.request.user)

    def perform_destroy(self, instance):
        # "Withdrawing" your own still-pending request -- once decided,
        # it's a permanent record (same convention as CustomerDeletionRequest).
        user = self.request.user
        if instance.status != InvoiceDeletionRequest.Status.PENDING and user.role not in (
            user.Role.MANAGEMENT, user.Role.ADMIN,
        ):
            raise MethodNotAllowed(
                "DELETE", detail="This request has already been decided and can no longer be withdrawn."
            )
        instance.delete()

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        deletion_request = self.get_object()
        if deletion_request.status != InvoiceDeletionRequest.Status.PENDING:
            return Response({"detail": "This request has already been decided."}, status=400)
        invoice = deletion_request.invoice
        if invoice is None:
            return Response({"detail": "This quote/pro forma invoice no longer exists."}, status=400)

        deletion_request.status = InvoiceDeletionRequest.Status.APPROVED
        deletion_request.decided_by = request.user
        deletion_request.decided_at = timezone.now()
        deletion_request.save(update_fields=["status", "decided_by", "decided_at"])

        # A quote/pro forma carries no balance debit, so this is a no-op
        # today -- called anyway so the invariant "nothing is deleted
        # without releasing its debit" holds at every delete site rather
        # than depending on which statuses reach this one.
        invoice.release_balance_debit()
        invoice.delete()

        return Response(InvoiceDeletionRequestSerializer(deletion_request).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        deletion_request = self.get_object()
        if deletion_request.status != InvoiceDeletionRequest.Status.PENDING:
            return Response({"detail": "This request has already been decided."}, status=400)
        deletion_request.status = InvoiceDeletionRequest.Status.REJECTED
        deletion_request.decision_note = request.data.get("decision_note", "")
        deletion_request.decided_by = request.user
        deletion_request.decided_at = timezone.now()
        deletion_request.save(update_fields=["status", "decision_note", "decided_by", "decided_at"])
        return Response(InvoiceDeletionRequestSerializer(deletion_request).data)


class PaymentMethodViewSet(viewsets.ModelViewSet):
    """Config-managed lookup of payment conventions (see PaymentMethod's
    docstring) -- any staff with Finance access can view them (needed for
    the billing-config dropdowns), but only Admin can add/edit/remove one,
    same trust tier as Email/OVPN settings."""

    serializer_class = PaymentMethodSerializer
    queryset = PaymentMethod.objects.all()
    filterset_fields = ["is_active"]

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [permissions.IsAuthenticated(), IsStaffMember(), HasFinanceAccess()]
        return [permissions.IsAuthenticated(), IsAdmin(), HasFinanceAccess()]


class BillingDefaultsView(APIView):
    """Admin-only: the org-wide recurring-billing template, edited from
    Configs -> Billing Defaults. GET/PATCH against the singleton row (see
    BillingDefaults.load()) -- same convention as EmailSettingsView/
    OvpnSettingsView. See apply_to_existing below for the bulk-copy action."""

    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def get(self, request):
        return Response(BillingDefaultsSerializer(BillingDefaults.load(), context={"request": request}).data)

    def patch(self, request):
        serializer = BillingDefaultsSerializer(
            BillingDefaults.load(), data=request.data, partial=True, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class ApplyBillingDefaultsView(APIView):
    """"Apply to existing customers" -- copies every SHARED_FIELDS value
    from BillingDefaults onto every customer who already has a
    CustomerBillingConfig row. Deliberately never touches billing_enabled
    (see both models' docstrings) and never creates new config rows for
    customers who've never touched theirs -- those get seeded from
    whatever the defaults are at the moment they're first opened, via
    CustomerBillingConfig.for_customer()."""

    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def post(self, request):
        defaults = BillingDefaults.load()
        updated = 0
        for config in CustomerBillingConfig.objects.all():
            config.apply_defaults(defaults)
            updated += 1
        return Response({"updated": updated})


class ReminderSettingsView(APIView):
    """Admin-only: global reminder kill-switches, edited from Configs ->
    Reminders. See ReminderSettings' docstring for why these can't be
    overridden per customer."""

    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def get(self, request):
        return Response(ReminderSettingsSerializer(ReminderSettings.load()).data)

    def patch(self, request):
        serializer = ReminderSettingsSerializer(ReminderSettings.load(), data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class SuspensionSettingsView(APIView):
    """The platform-wide auto-suspension master switch, edited from Configs
    -> Billing -> Auto-suspension. See SuspensionSettings' docstring for
    why this can't be overridden per customer. GET is available to any
    staff with Finance access (so Finance -> Recurring Billing can show
    whether a Run would actually suspend anyone before staff click Run);
    only Admin can flip the switch itself, same trust tier as Billing
    Defaults / Reminder Settings."""

    def get_permissions(self):
        if self.request.method == "GET":
            return [permissions.IsAuthenticated(), IsStaffMember(), HasFinanceAccess()]
        return [permissions.IsAuthenticated(), IsAdmin()]

    def get(self, request):
        return Response(SuspensionSettingsSerializer(SuspensionSettings.load()).data)

    def patch(self, request):
        serializer = SuspensionSettingsSerializer(SuspensionSettings.load(), data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class CustomerBillingConfigView(APIView):
    """Per-customer recurring-billing configuration -- reached from that
    customer's own detail page. Lazily created (seeded from
    BillingDefaults) the first time it's fetched, same convention as
    CustomerBillingConfig.for_customer(). Any staff with Finance access can
    view/edit it -- not further restricted, same trust tier as editing a
    customer's Services or Invoices."""

    permission_classes = [permissions.IsAuthenticated, IsStaffMember, HasFinanceAccess]

    def get_object(self, customer_id):
        from customers.models import Customer
        customer = get_object_or_404(Customer, pk=customer_id)
        return CustomerBillingConfig.for_customer(customer)

    def get(self, request, customer_id):
        return Response(CustomerBillingConfigSerializer(self.get_object(customer_id)).data)

    def patch(self, request, customer_id):
        config = self.get_object(customer_id)
        serializer = CustomerBillingConfigSerializer(config, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class RecurringBillingRunViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only History list for Finance -> Recurring Billing -- rows are
    only ever created by RecurringBillingViewSet.run below (see
    billing.recurring.run_recurring_billing)."""

    serializer_class = RecurringBillingRunSerializer
    queryset = RecurringBillingRun.objects.prefetch_related("partners").select_related("triggered_by").all()
    permission_classes = [permissions.IsAuthenticated, IsStaffMember, HasFinanceAccess]
    filterset_fields = ["status"]


class RecurringBillingViewSet(viewsets.ViewSet):
    """Preview/Run actions for the recurring-billing engine -- Finance ->
    Recurring Billing's Preview/Run buttons. See
    billing.recurring.run_recurring_billing for the actual logic; this is
    just the thin HTTP wrapper around it."""

    permission_classes = [permissions.IsAuthenticated, IsStaffMember, HasFinanceAccess]

    def _parse_args(self, request):
        from datetime import date as date_cls
        run_date_str = request.data.get("date")
        if not run_date_str:
            raise ValueError("A date is required.")
        run_date = date_cls.fromisoformat(run_date_str)
        partner_ids = request.data.get("partners") or []
        return run_date, [int(p) for p in partner_ids]

    @action(detail=False, methods=["post"])
    def preview(self, request):
        from .recurring import run_recurring_billing
        try:
            run_date, partner_ids = self._parse_args(request)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        result = run_recurring_billing(run_date, partner_ids=partner_ids or None, commit=False)
        return Response({"counts": result["counts"]})

    @action(detail=False, methods=["post"])
    def run(self, request):
        from .recurring import run_recurring_billing
        try:
            run_date, partner_ids = self._parse_args(request)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        result = run_recurring_billing(
            run_date, partner_ids=partner_ids or None, commit=True, triggered_by=request.user
        )
        return Response(RecurringBillingRunSerializer(result["run"]).data)


class PaymentViewSet(ScopedByCustomerMixin, viewsets.ModelViewSet):
    serializer_class = PaymentSerializer
    permission_classes = [permissions.IsAuthenticated]
    filterset_fields = ["method", "customer", "invoice"]
    ordering_fields = [
        "date",
        "customer__full_name",
        "amount",
        "method",
        "received_by__username",
    ]

    def get_queryset(self):
        return self.get_base_queryset(Payment).select_related("customer", "invoice")

    def get_permissions(self):
        # HasFinanceAccess passes straight through for customers (section
        # restrictions are staff-only) so this only narrows list/retrieve
        # for staff who lack the Finance section, without touching what
        # customers can see of their own payments in the portal.
        if self.action in ("list", "retrieve"):
            return [permissions.IsAuthenticated(), HasFinanceAccess()]
        return [permissions.IsAuthenticated(), IsStaffMember(), HasFinanceAccess()]

    def perform_destroy(self, instance):
        # Deleting a payment has to put the ledger back where it was --
        # see Payment.reverse_ledger_effect for what used to happen
        # instead. Both writes and the delete go in one transaction so a
        # failure can't leave the balance reversed with the payment still
        # on file, or vice versa.
        with transaction.atomic():
            instance.reverse_ledger_effect()
            instance.delete()


class CreditRequestViewSet(viewsets.ModelViewSet):
    """Credit requests are a shared finance queue for Accounts/Management
    (and Admin) -- not scoped per-user like Leave requests, since it's a
    team workflow rather than personal HR data. Only Accounts can submit
    one; only Management can decide it."""

    serializer_class = CreditRequestSerializer
    queryset = CreditRequest.objects.select_related("customer", "requested_by", "decided_by").all()
    filterset_fields = ["customer", "status"]

    def get_queryset(self):
        # Partner-scoped like everything else that names a customer; a
        # credit request carries the customer's name and the amount.
        from customers.models import Customer
        from customers.views import scope_customers_to_user

        qs = super().get_queryset()
        user = self.request.user
        allowed = getattr(user, "allowed_partners", None) or []
        if not allowed or user.role == user.Role.ADMIN:
            return qs
        return qs.filter(customer__in=scope_customers_to_user(Customer.objects.all(), user))

    def get_permissions(self):
        if self.action in ("approve", "reject"):
            return [permissions.IsAuthenticated(), IsManagement(), HasFinanceAccess()]
        if self.action == "create":
            return [permissions.IsAuthenticated(), IsAccounts(), HasFinanceAccess()]
        return [permissions.IsAuthenticated(), IsAccountsOrManagement(), HasFinanceAccess()]

    def perform_create(self, serializer):
        serializer.save(requested_by=self.request.user)

    def perform_update(self, serializer):
        instance = serializer.instance
        user = self.request.user
        if instance.status != CreditRequest.Status.PENDING and user.role not in (
            user.Role.MANAGEMENT, user.Role.ADMIN,
        ):
            raise PermissionDenied("This credit request has already been decided and can no longer be edited.")
        serializer.save()

    def perform_destroy(self, instance):
        user = self.request.user
        if instance.status != CreditRequest.Status.PENDING and user.role not in (
            user.Role.MANAGEMENT, user.Role.ADMIN,
        ):
            raise PermissionDenied("This credit request has already been decided and can no longer be withdrawn.")
        instance.delete()

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        """Credit the customer, once.

        This used to read the request, check it was Pending, write the
        balance and then write the decision -- four steps in autocommit
        with no row lock. Two Approve clicks 50 ms apart both passed the
        Pending check and both credited, so a R200 credit on a R1,000
        balance left R600. And if the decision save failed after the
        balance write, the customer kept the credit with the request still
        Pending, ready to be approved again.

        The request row is now locked for the duration and re-read inside
        the lock, so the second click finds it Approved and stops -- and
        the balance write uses F() rather than a read-modify-write, which
        is what let a concurrent payment lose a credit (or vice versa).
        bankfeeds.confirm() has used select_for_update for exactly this
        "double-clicked button could post the same money twice" hazard all
        along.
        """
        from django.db.models import F

        from customers.models import Customer

        with transaction.atomic():
            credit = CreditRequest.objects.select_for_update().get(pk=self.get_object().pk)
            if credit.status != CreditRequest.Status.PENDING:
                return Response({"detail": "This request has already been decided."}, status=400)

            Customer.objects.filter(pk=credit.customer_id).update(
                balance=F("balance") - credit.amount
            )
            credit.status = CreditRequest.Status.APPROVED
            credit.decided_by = request.user
            credit.decided_at = timezone.now()
            credit.save(update_fields=["status", "decided_by", "decided_at"])

        credit.refresh_from_db()
        return Response(CreditRequestSerializer(credit).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        credit = self.get_object()
        if credit.status != CreditRequest.Status.PENDING:
            return Response({"detail": "This request has already been decided."}, status=400)
        credit.status = CreditRequest.Status.REJECTED
        credit.decision_note = request.data.get("decision_note", "")
        credit.decided_by = request.user
        credit.decided_at = timezone.now()
        credit.save(update_fields=["status", "decision_note", "decided_by", "decided_at"])
        return Response(CreditRequestSerializer(credit).data)


class UpcomingBlocksView(APIView):
    """GET /api/upcoming-blocks/?days=7

    Customers heading for an auto-suspension -- the dashboard's "Blocking
    tomorrow" tile and its drill-down list.

    The headline figure is TOMORROW (`count_tomorrow`); `results` covers the
    whole horizon so there is a chase window rather than a same-day surprise,
    with each customer's own block date and how many days away it is.

    Deliberately reports `auto_suspend_enabled` rather than being gated on
    it. The master switch (Configs -> Billing -> Auto-suspension) is off by
    default, and a panel that returned zero while it was off would be useless
    precisely when it matters most -- these are customers worth chasing
    whether or not the automation will actually cut them off. The frontend
    says plainly which it is.

    Uses billing.recurring.blocking_candidate_services' own predicate (via
    upcoming_blocks), so this cannot disagree with what a real billing run
    would do.

    Respects the same partner-visibility restriction as CustomerViewSet (see
    User.allowed_partners): a reseller-scoped staff member must not learn the
    names of customers outside their partners just because those customers
    turn up on a dashboard aggregate.
    """

    permission_classes = [permissions.IsAuthenticated, IsStaffMember, HasFinanceAccess]

    def get(self, request):
        from datetime import timedelta

        from django.db.models import Q as _Q

        from customers.models import Customer
        from .recurring import upcoming_blocks

        try:
            days = int(request.query_params.get("days", 7))
        except (TypeError, ValueError):
            return Response({"detail": "days must be a whole number."}, status=400)
        days = max(1, min(days, 60))

        # Mirror CustomerViewSet.get_queryset's partner scoping.
        user = request.user
        visible = Customer.objects.all()
        allowed = getattr(user, "allowed_partners", None) or []
        if allowed and user.role != user.Role.ADMIN:
            visible = visible.filter(_Q(partner_id__in=allowed) | _Q(partner__isnull=True))

        tomorrow = timezone.localdate() + timedelta(days=1)
        results = upcoming_blocks(tomorrow, horizon_days=days, customers=visible)
        suspension_settings = SuspensionSettings.load()

        return Response({
            "from_date": tomorrow,
            "horizon_days": days,
            "auto_suspend_enabled": suspension_settings.auto_suspend_enabled,
            "count_tomorrow": sum(1 for r in results if r["days_until"] == 0),
            "count_horizon": len(results),
            "results": results,
        })
