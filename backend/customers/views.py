from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework import viewsets, permissions
from .models import Customer
from .serializers import CustomerSerializer
from accounts.permissions import IsStaffMember
from config.csv_import import CSVImportMixin

User = get_user_model()


class CustomerViewSet(CSVImportMixin, viewsets.ModelViewSet):
    serializer_class = CustomerSerializer
    filterset_fields = ["status", "category", "customer_type"]
    search_fields = ["full_name", "company_name", "email", "phone", "customer_id"]
    ordering_fields = ["created_at", "full_name", "balance"]

    import_model = Customer
    import_fields = {
        "full_name": {"required": True},
        "company_name": {"default": ""},
        "email": {"default": ""},
        "phone": {"default": ""},
        "address": {"default": ""},
        "city": {"default": ""},
        "zip_code": {"default": ""},
        "customer_type": {
            "default": Customer.CustomerType.INDIVIDUAL,
            "choices": Customer.CustomerType.values,
        },
        "category": {
            "default": Customer.Category.RESIDENTIAL,
            "choices": Customer.Category.values,
        },
        "status": {"default": Customer.Status.NEW, "choices": Customer.Status.values},
        "balance": {"type": "decimal", "default": Decimal("0")},
        "assigned_staff_username": {"default": ""},
        "notes": {"default": ""},
    }

    def extra_row_validation(self, cleaned, raw_row):
        errors = []
        username = (cleaned.pop("assigned_staff_username", "") or "").strip()
        if username:
            staff = User.objects.filter(username=username, role__in=["admin", "staff", "technician"]).first()
            if staff is None:
                errors.append(f"No staff user found with username '{username}' for 'assigned_staff_username'")
            else:
                cleaned["assigned_staff"] = staff
        else:
            cleaned["assigned_staff"] = None
        return errors

    def get_permissions(self):
        if self.action == "retrieve":
            return [permissions.IsAuthenticated()]
        return [permissions.IsAuthenticated(), IsStaffMember()]

    def get_queryset(self):
        user = self.request.user
        qs = Customer.objects.select_related("assigned_staff").all()
        if user.is_staff_member:
            return qs
        customer_profile = getattr(user, "customer_profile", None)
        if customer_profile is None:
            return qs.none()
        return qs.filter(pk=customer_profile.pk)
