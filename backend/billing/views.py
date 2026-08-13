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

    def get_queryset(self):
        return self.get_base_queryset(Service).select_related("tariff", "customer")

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [permissions.IsAuthenticated()]
        return [permissions.IsAuthenticated(), IsStaffMember()]


class InvoiceViewSet(ScopedByCustomerMixin, viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    filterset_fields = ["status", "customer"]

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
