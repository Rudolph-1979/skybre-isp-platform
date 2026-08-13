from django.contrib import admin
from .models import Device, IPPool, IPAddress, MonitoringReading


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = ("name", "device_type", "ip_address", "status", "location")
    list_filter = ("device_type", "status")


@admin.register(IPPool)
class IPPoolAdmin(admin.ModelAdmin):
    list_display = ("name", "network_cidr", "pool_type")


@admin.register(IPAddress)
class IPAddressAdmin(admin.ModelAdmin):
    list_display = ("address", "pool", "status", "assigned_service")
    list_filter = ("status",)


@admin.register(MonitoringReading)
class MonitoringReadingAdmin(admin.ModelAdmin):
    list_display = ("device", "timestamp", "is_up", "latency_ms", "bandwidth_in_mbps", "bandwidth_out_mbps")
    list_filter = ("is_up",)
