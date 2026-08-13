from rest_framework import viewsets, permissions
from .models import Device, IPPool, IPAddress, MonitoringReading
from .serializers import DeviceSerializer, IPPoolSerializer, IPAddressSerializer, MonitoringReadingSerializer
from accounts.permissions import IsStaffMember


class DeviceViewSet(viewsets.ModelViewSet):
    serializer_class = DeviceSerializer
    queryset = Device.objects.prefetch_related("readings").all()
    permission_classes = [permissions.IsAuthenticated, IsStaffMember]
    filterset_fields = ["device_type", "status"]
    search_fields = ["name", "ip_address", "location"]


class IPPoolViewSet(viewsets.ModelViewSet):
    serializer_class = IPPoolSerializer
    queryset = IPPool.objects.all()
    permission_classes = [permissions.IsAuthenticated, IsStaffMember]


class IPAddressViewSet(viewsets.ModelViewSet):
    serializer_class = IPAddressSerializer
    queryset = IPAddress.objects.all()
    permission_classes = [permissions.IsAuthenticated, IsStaffMember]
    filterset_fields = ["status", "pool"]


class MonitoringReadingViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = MonitoringReadingSerializer
    permission_classes = [permissions.IsAuthenticated, IsStaffMember]
    filterset_fields = ["device"]

    def get_queryset(self):
        qs = MonitoringReading.objects.select_related("device").all()
        limit = self.request.query_params.get("limit")
        if limit:
            qs = qs[: int(limit)]
        return qs
