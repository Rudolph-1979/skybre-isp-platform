from datetime import timedelta
from django.contrib.auth.hashers import check_password
from django.utils import timezone
from django.db.models import Sum, Count, Q
from rest_framework import generics, serializers, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.exceptions import TokenError, InvalidToken
from rest_framework_simplejwt.views import TokenObtainPairView
from .models import User, TwoFactorAuth
from .serializers import CustomTokenObtainPairSerializer, UserSerializer
from .permissions import IsStaffMember
from . import two_factor


def _flatten_detail(value):
    """DRF wraps ValidationError dict values in lists of ErrorDetail
    (str subclasses) for i18n/multi-error support. Our 2FA error shapes
    are always single plain strings, so unwrap back to that — the
    frontend checks `response.data.code` as a plain string."""
    if isinstance(value, (list, tuple)):
        return str(value[0]) if value else ""
    return str(value)


class CustomTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
        except TokenError as e:
            raise InvalidToken(e.args[0]) from e
        except serializers.ValidationError as e:
            detail = e.detail
            if isinstance(detail, dict) and "code" in detail:
                return Response(
                    {"code": _flatten_detail(detail["code"]), "detail": _flatten_detail(detail.get("detail", ""))},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            raise
        return Response(serializer.validated_data, status=status.HTTP_200_OK)


class StaffListView(generics.ListAPIView):
    """Read-only list of staff/admin/technician users, for assignment
    dropdowns — scheduling jobs/shifts, ticket assignment, etc."""

    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated, IsStaffMember]

    def get_queryset(self):
        return User.objects.filter(role__in=["admin", "staff", "technician"], is_active=True).order_by("username")


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        data = UserSerializer(request.user).data
        customer_profile = getattr(request.user, "customer_profile", None)
        if customer_profile is not None:
            data["customer_id"] = customer_profile.id
        return Response(data)


class TwoFactorStatusView(APIView):
    """Whether the current user has 2FA confirmed and active — the
    account settings page uses this to show Enable vs Disable."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        device = getattr(request.user, "two_factor", None)
        return Response({"enabled": bool(device and device.confirmed)})


class TwoFactorSetupView(APIView):
    """Starts (or restarts) 2FA setup: generates a fresh secret and QR
    code. Doesn't take effect until confirmed via TwoFactorConfirmView —
    calling this again before confirming just issues a new secret, so an
    abandoned setup can't leave stale state."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        existing = getattr(request.user, "two_factor", None)
        if existing is not None and existing.confirmed:
            return Response(
                {"detail": "Two-factor authentication is already enabled. Disable it first to reconfigure."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if existing is not None:
            existing.delete()

        secret = two_factor.generate_secret()
        device = TwoFactorAuth.objects.create(user=request.user, secret=secret)
        return Response(
            {
                "secret": secret,
                "qr_code": two_factor.qr_code_data_uri(secret, request.user.username),
            }
        )


class TwoFactorConfirmView(APIView):
    """Verifies the first code from the authenticator app and activates
    2FA. Returns one-time backup codes — shown to the user exactly once."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        device = getattr(request.user, "two_factor", None)
        if device is None or device.confirmed:
            return Response(
                {"detail": "No two-factor setup in progress. Start setup again."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        code = request.data.get("code", "")
        if not two_factor.verify_totp_code(device.secret, code):
            return Response({"detail": "Invalid code — check the time on your phone and try again."}, status=400)

        device.confirmed = True
        device.confirmed_at = timezone.now()
        device.save(update_fields=["confirmed", "confirmed_at"])
        backup_codes = two_factor.generate_backup_codes(device)
        return Response({"detail": "Two-factor authentication enabled.", "backup_codes": backup_codes})


class TwoFactorDisableView(APIView):
    """Requires the account password again — disabling 2FA is a
    security-relevant action, not something a stolen access token alone
    should be able to do without re-proving the password."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        device = getattr(request.user, "two_factor", None)
        if device is None or not device.confirmed:
            return Response({"detail": "Two-factor authentication is not enabled."}, status=400)
        if not check_password(request.data.get("password", ""), request.user.password):
            return Response({"detail": "Incorrect password."}, status=400)
        device.delete()
        return Response({"detail": "Two-factor authentication disabled."})


class DashboardSummaryView(APIView):
    """Aggregate KPI numbers for the admin dashboard landing page."""

    permission_classes = [IsAuthenticated, IsStaffMember]

    def get(self, request):
        from customers.models import Customer
        from billing.models import Invoice, Payment, Service
        from network.models import Device
        from tickets.models import Ticket

        now = timezone.now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        revenue_this_month = Payment.objects.filter(date__gte=month_start).aggregate(total=Sum("amount"))["total"] or 0
        outstanding = Invoice.objects.filter(status__in=["unpaid", "overdue"]).aggregate(total=Sum("total"))["total"] or 0

        return Response({
            "customers_total": Customer.objects.count(),
            "customers_active": Customer.objects.filter(status="active").count(),
            "services_active": Service.objects.filter(status="active").count(),
            "revenue_this_month": revenue_this_month,
            "outstanding_balance": outstanding,
            "invoices_unpaid": Invoice.objects.filter(status="unpaid").count(),
            "invoices_overdue": Invoice.objects.filter(status="overdue").count(),
            "devices_total": Device.objects.count(),
            "devices_online": Device.objects.filter(status="online").count(),
            "devices_offline": Device.objects.filter(status="offline").count(),
            "tickets_open": Ticket.objects.filter(status__in=["open", "pending"]).count(),
            "tickets_urgent": Ticket.objects.filter(priority="urgent").exclude(status__in=["resolved", "closed"]).count(),
        })
