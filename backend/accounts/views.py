from datetime import timedelta
from django.utils import timezone
from django.db.models import Sum, Count, Q
from rest_framework import generics
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.views import TokenObtainPairView
from .models import User
from .serializers import CustomTokenObtainPairSerializer, UserSerializer
from .permissions import IsStaffMember


class CustomTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer


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
