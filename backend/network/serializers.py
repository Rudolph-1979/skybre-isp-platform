from rest_framework import serializers
from customers.models import Partner
from .models import Device, NetworkSite, IPPool, IPAddress, ConnectionRule, MonitoringReading


class NetworkSiteSerializer(serializers.ModelSerializer):
    # A list, and "empty means all" -- same convention as
    # Device.visible_partners, so the two never need explaining differently.
    partner_names = serializers.SerializerMethodField()
    hardware_count = serializers.SerializerMethodField()

    class Meta:
        model = NetworkSite
        fields = [
            "id", "title", "contact_person", "phone", "address", "location",
            "partners", "partner_names", "notes", "created_at", "hardware_count",
        ]
        read_only_fields = ["id", "created_at"]

    def get_partner_names(self, obj):
        return [p.name for p in obj.partners.all()]

    def get_hardware_count(self, obj):
        return obj.devices.count()


class ConnectionRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConnectionRule
        fields = ["id", "device", "title", "speed_down_kbps", "speed_up_kbps", "guaranteed_pct", "created_at"]
        read_only_fields = ["id", "created_at"]


class DeviceSerializer(serializers.ModelSerializer):
    latest_reading = serializers.SerializerMethodField()
    # Same write-only-secret pattern used elsewhere (StaffAccounts,
    # EmailSettings, Service.radius_password, RadiusNasClient.secret):
    # accept the API password on create/update, never echo it back;
    # api_password_set tells the frontend whether one's on file.
    api_password = serializers.CharField(write_only=True, required=False, allow_blank=True, allow_null=True)
    api_password_set = serializers.SerializerMethodField()
    site_name = serializers.CharField(source="site.title", read_only=True, default=None)
    # Which partners' staff can see this device (see Device.visible_partners
    # docstring) -- plain PK list is enough here, same treatment as
    # billing.Service's other list-of-ids fields.
    visible_partners = serializers.PrimaryKeyRelatedField(many=True, queryset=Partner.objects.all(), required=False)
    visible_partner_names = serializers.SerializerMethodField()
    # How many live customer lines connect THROUGH this box
    # (Service.access_device). Shown on the device list so the cost of an
    # outage is visible before anyone opens anything -- and so a device
    # nobody has recorded connections against reads as 0 rather than
    # looking the same as one carrying forty customers.
    access_service_count = serializers.SerializerMethodField()

    class Meta:
        model = Device
        fields = [
            "id", "name", "device_type", "ip_address", "location", "site", "site_name", "vendor", "model_name",
            "status", "snmp_community", "snmp_version", "created_at", "latest_reading",
            "api_enabled", "api_port", "api_username", "api_password", "api_password_set", "api_use_ssl",
            "api_wan_interface", "visible_partners", "visible_partner_names",
            "block_disabled_customers", "enable_shaper", "shaping_type",
            "enable_wireless_access_list", "enable_mpsk", "wireless_interface",
            "access_service_count",
        ]
        read_only_fields = ["id", "created_at"]

    def get_access_service_count(self, obj):
        from billing.models import Service

        # Annotated by the list view where one exists; falls back to a
        # count so a single retrieve() still answers correctly.
        annotated = getattr(obj, "access_service_total", None)
        if annotated is not None:
            return annotated
        return Service.objects.filter(access_device=obj).exclude(
            status=Service.Status.TERMINATED
        ).count()

    def get_visible_partner_names(self, obj):
        return list(obj.visible_partners.values_list("name", flat=True))

    def get_api_password_set(self, obj):
        return bool(obj.api_password)

    def get_api_password_set(self, obj):
        return bool(obj.api_password)

    def update(self, instance, validated_data):
        # Blank/omitted api_password on an edit means "keep the existing
        # one" -- only a non-empty value actually overwrites it.
        if "api_password" in validated_data and not validated_data["api_password"]:
            validated_data.pop("api_password")
        return super().update(instance, validated_data)

    def get_latest_reading(self, obj):
        reading = obj.readings.first()
        if not reading:
            return None
        return {
            "timestamp": reading.timestamp,
            "is_up": reading.is_up,
            "latency_ms": reading.latency_ms,
            "packet_loss_pct": reading.packet_loss_pct,
            "bandwidth_in_mbps": reading.bandwidth_in_mbps,
            "bandwidth_out_mbps": reading.bandwidth_out_mbps,
            "cpu_pct": reading.cpu_pct,
            "memory_pct": reading.memory_pct,
        }


class IPPoolSerializer(serializers.ModelSerializer):
    free_count = serializers.SerializerMethodField()
    total_count = serializers.SerializerMethodField()
    used_pct = serializers.SerializerMethodField()
    root_net_name = serializers.CharField(source="root_net.name", read_only=True, default=None)

    class Meta:
        model = IPPool
        fields = [
            "id", "name", "network_cidr", "gateway", "pool_type", "category",
            "network_type", "root_net", "root_net_name",
            "description", "free_count", "total_count", "used_pct",
        ]

    def get_free_count(self, obj):
        return obj.addresses.filter(status="free").count()

    def get_total_count(self, obj):
        return obj.addresses.count()

    def get_used_pct(self, obj):
        total = obj.addresses.count()
        if not total:
            return 0
        used = total - obj.addresses.filter(status="free").count()
        return round(used / total * 100)

    def validate_network_cidr(self, value):
        """Reject a CIDR the address rows would no longer belong to.

        Editing a pool's name, gateway or category is harmless, but moving
        its CIDR while addresses exist would strand every one of them
        outside the range they were generated from -- including addresses
        currently assigned to customers as their Framed-IP-Address. The
        pool would keep handing out addresses from a network it no longer
        claims, silently. Free addresses can be cleared and regenerated;
        assigned ones have to be released first, which is the same rule
        IPPoolViewSet.perform_destroy applies to deletion.
        """
        instance = self.instance
        if instance is None or value == instance.network_cidr:
            return value

        total = instance.addresses.count()
        if total:
            in_use = instance.addresses.exclude(status="free").count()
            detail = (
                f"{in_use} of them are assigned or reserved, so release those first "
                if in_use
                else "Use \"Clear unused addresses\" first, then regenerate from the new range "
            )
            raise serializers.ValidationError(
                f"This pool already holds {total} address(es) generated from {instance.network_cidr}. "
                f"{detail}-- changing the range underneath them would leave them pointing outside it."
            )
        return value

    def validate_root_net(self, value):
        if value is not None and self.instance is not None and value.pk == self.instance.pk:
            raise serializers.ValidationError("A network can't be its own RootNet.")
        return value


class IPAddressSerializer(serializers.ModelSerializer):
    class Meta:
        model = IPAddress
        fields = ["id", "pool", "address", "status", "assigned_service"]
        read_only_fields = ["id"]


class MonitoringReadingSerializer(serializers.ModelSerializer):
    class Meta:
        model = MonitoringReading
        fields = [
            "id", "device", "timestamp", "is_up", "latency_ms", "packet_loss_pct",
            "bandwidth_in_mbps", "bandwidth_out_mbps", "cpu_pct", "memory_pct",
        ]
