from django.db import models


class Device(models.Model):
    class DeviceType(models.TextChoices):
        ROUTER = "router", "Router"
        SWITCH = "switch", "Switch"
        OLT = "olt", "OLT"
        AP = "access_point", "Access Point"
        SERVER = "server", "Server"
        ONU = "onu", "ONU/CPE"

    class Status(models.TextChoices):
        ONLINE = "online", "Online"
        OFFLINE = "offline", "Offline"
        UNKNOWN = "unknown", "Unknown"

    name = models.CharField(max_length=150)
    device_type = models.CharField(max_length=20, choices=DeviceType.choices, default=DeviceType.ROUTER)
    ip_address = models.GenericIPAddressField()
    location = models.CharField(max_length=255, blank=True)
    vendor = models.CharField(max_length=100, blank=True)
    model_name = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.UNKNOWN)
    snmp_community = models.CharField(
        max_length=100, blank=True, default="public",
        help_text="SNMP read community string. Wire real polling to network.monitoring against this field.",
    )
    snmp_version = models.CharField(max_length=10, default="2c")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.ip_address})"


class IPPool(models.Model):
    class PoolType(models.TextChoices):
        IPV4 = "ipv4", "IPv4"
        IPV6 = "ipv6", "IPv6"

    name = models.CharField(max_length=150)
    network_cidr = models.CharField(max_length=64, help_text="e.g. 10.10.0.0/24")
    gateway = models.GenericIPAddressField(null=True, blank=True)
    pool_type = models.CharField(max_length=10, choices=PoolType.choices, default=PoolType.IPV4)
    description = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return f"{self.name} ({self.network_cidr})"


class IPAddress(models.Model):
    class Status(models.TextChoices):
        FREE = "free", "Free"
        ASSIGNED = "assigned", "Assigned"
        RESERVED = "reserved", "Reserved"

    pool = models.ForeignKey(IPPool, on_delete=models.CASCADE, related_name="addresses")
    address = models.GenericIPAddressField(unique=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.FREE)
    assigned_service = models.ForeignKey(
        "billing.Service", on_delete=models.SET_NULL, null=True, blank=True, related_name="ip_addresses"
    )

    class Meta:
        ordering = ["address"]
        verbose_name_plural = "IP addresses"

    def __str__(self):
        return self.address


class MonitoringReading(models.Model):
    """Point-in-time health/performance reading for a device.

    In production this row is populated by a scheduled task that polls the
    device over SNMP (see docs/NETWORK_MONITORING.md). For this build, a
    management command (`simulate_monitoring`) generates realistic synthetic
    readings so the dashboards and charts have live data to render.
    """

    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="readings")
    timestamp = models.DateTimeField(db_index=True)
    is_up = models.BooleanField(default=True)
    latency_ms = models.FloatField(null=True, blank=True)
    packet_loss_pct = models.FloatField(null=True, blank=True)
    bandwidth_in_mbps = models.FloatField(null=True, blank=True)
    bandwidth_out_mbps = models.FloatField(null=True, blank=True)
    cpu_pct = models.FloatField(null=True, blank=True)
    memory_pct = models.FloatField(null=True, blank=True)

    class Meta:
        ordering = ["-timestamp"]
        indexes = [models.Index(fields=["device", "-timestamp"])]

    def __str__(self):
        return f"{self.device} @ {self.timestamp}"
