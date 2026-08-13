from decimal import Decimal

from rest_framework import viewsets, permissions
from .models import Tariff, Service, Invoice, Payment
from .serializers import (
    TariffSerializer, ServiceSerializer, InvoiceSerializer,
    InvoiceCreateSerializer, PaymentSerializer,
)
from accounts.permissions import IsStaffMember
from config.csv_import import CSVImportMixin


class TariffViewSet(CSVImportMixin, viewsets.ModelViewSet):
    serializer_class = TariffSerializer
    queryset = Tariff.objects.all()
    filterset_fields = ["service_type", "is_active"]
    search_fields = ["name"]
    ordering_fields = [
        "name", "service_type", "price", "billing_period",
        "speed_download_mbps", "tax_rate_pct", "is_active", "created_at",
    ]

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
        "speed_download_mbps": {"type": "int", "default": None},
        "speed_upload_mbps": {"type": "int", "default": None},
        "data_cap_gb": {"type": "int", "default": None},
        "tax_rate_pct": {"type": "decimal", "default": Decimal("0")},
        "is_active": {"type": "bool", "default": True},
        "description": {"default": ""},
    }

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [permissions.IsAuthenticated()]
        return [permissions.IsAuthenticated(), IsStaffMember()]


class ScopedByCustomerMixin:
    """Staff see everything; customers only see records tied to their own profile."""

    def get_base_queryset(self, model, related_name="customer"):
        user = self.request.user
        qs = model.objects.all()
        if user.is_staff_member:
            return qs
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
        return self.get_base_queryset(Service).select_related("tariff", "customer")

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [permissions.IsAuthenticated()]
        return [permissions.IsAuthenticated(), IsStaffMember()]


class InvoiceViewSet(ScopedByCustomerMixin, viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    filterset_fields = ["status", "customer"]
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
        if self.action in ("list", "retrieve"):
            return [permissions.IsAuthenticated()]
        return [permissions.IsAuthenticated(), IsStaffMember()]


class PaymentViewSet(ScopedByCustomerMixin, viewsets.ModelViewSet):
    serializer_class = PaymentSerializer
    permission_classes = [permissions.IsAuthenticated]
    filterset_fields = ["method", "customer", "invoice"]

    def get_queryset(self):
        return self.get_base_queryset(Payment).select_related("customer", "invoice")

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [permissions.IsAuthenticated()]
        return [permissions.IsAuthenticated(), IsStaffMember()]
