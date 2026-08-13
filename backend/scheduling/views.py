from rest_framework import viewsets, permissions
from .models import Job, Shift
from .serializers import JobSerializer, ShiftSerializer
from .filters import JobFilter, ShiftFilter
from accounts.permissions import IsStaffMember


class JobViewSet(viewsets.ModelViewSet):
    """Field jobs and standalone tasks. Internal ops tooling — scoped to
    staff/admin/technician only; customers never see this."""

    serializer_class = JobSerializer
    permission_classes = [permissions.IsAuthenticated, IsStaffMember]
    filterset_class = JobFilter
    ordering_fields = [
        "start", "end", "status", "job_type",
        "customer__full_name", "assigned_to__username",
    ]

    def get_queryset(self):
        return Job.objects.select_related("customer", "assigned_to", "ticket").all()


class ShiftViewSet(viewsets.ModelViewSet):
    serializer_class = ShiftSerializer
    permission_classes = [permissions.IsAuthenticated, IsStaffMember]
    filterset_class = ShiftFilter
    ordering_fields = ["start", "end", "status", "staff__username"]

    def get_queryset(self):
        return Shift.objects.select_related("staff").all()
