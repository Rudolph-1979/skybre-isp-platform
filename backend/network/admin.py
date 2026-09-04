from django.contrib import admin
from .models import Device, IPPool, IPAddress, MonitoringReading


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = ("name", "device_type", "ip_address", "status", "location")
    list_filter = ("device_type", "status")
    # The REST API makes both of these write_only, with a *_set boolean so
    # the UI can say whether one is on file without ever echoing it back
    # (network/serializers.py). The admin's default form has no such
    # notion, so it rendered the router's admin password and the SNMP
    # community string as plain, editable text inputs -- undoing that work
    # for anyone who can reach /django-admin/, which until now was
    # everybody. api_password is write access to the devices carrying
    # subscriber traffic.
    #
    # Excluded rather than made read-only: readonly_fields still RENDERS
    # the value, which is the same mistake TwoFactorAuthAdmin was making.
    # Credentials are set through the Networking page.
    exclude = ("api_password", "snmp_community")


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
