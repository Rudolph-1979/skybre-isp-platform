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
        # "aliases" let this same import accept a raw Splynx customer-list
        # export (tab-delimited, its own column names) without any manual
        # reformatting — as well as our own plain template.
        "full_name": {"required": True, "aliases": ["Full name"]},
        "company_name": {"default": ""},
        "email": {"default": ""},
        "phone": {"default": ""},
        "address": {"default": "", "aliases": ["Street"]},
        "city": {"default": "", "aliases": ["City"]},
        "zip_code": {"default": ""},
        "customer_type": {
            "default": Customer.CustomerType.INDIVIDUAL,
            "choices": Customer.CustomerType.values,
        },
        "category": {
            "default": Customer.Category.RESIDENTIAL,
            "choices": Customer.Category.values,
        },
        # Deliberately NOT aliased to Splynx's "Status" column — that field
        # is network connectivity (Online/Offline), not account/billing
        # status, so it would be actively misleading to map it here.
        "status": {"default": Customer.Status.NEW, "choices": Customer.Status.values},
        "balance": {"type": "decimal", "default": Decimal("0"), "aliases": ["Account balance"]},
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

        # If this looks like a Splynx export, preserve traceability back to
        # the source record (its internal ID, portal login, and current
        # plan aren't fields on our Customer model) in the notes field
        # rather than silently dropping them. This also lets us catch the
        # same customer appearing in more than one export file (Splynx's
        # portal login is effectively a unique customer key) and skip the
        # duplicate instead of creating a second record.
        portal_login = raw_row.get("Portal login")
        if portal_login and Customer.objects.filter(notes__icontains=f"Portal login: {portal_login}").exists():
            errors.append(f"Already imported previously (Portal login '{portal_login}' already exists)")

        if not cleaned.get("notes"):
            splynx_id = raw_row.get("ID")
            plan = raw_row.get("Internet plans")
            parts = []
            if splynx_id:
                parts.append(f"Splynx ID: {splynx_id}")
            if portal_login:
                parts.append(f"Portal login: {portal_login}")
            if plan:
                parts.append(f"Plan at import: {plan}")
            if parts:
                cleaned["notes"] = " | ".join(parts)

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
