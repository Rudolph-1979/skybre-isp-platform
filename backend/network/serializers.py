from rest_framework import serializers
from .models import Device, IPPool, IPAddress, MonitoringReading


class DeviceSerializer(serializers.ModelSerializer):
    latest_reading = serializers.SerializerMethodField()

    class Meta:
        model = Device
        fields = [
            "id", "name", "device_type", "ip_address", "location", "vendor", "model_name",
            "status", "snmp_community", "snmp_version", "created_at", "latest_reading",
        ]
        read_only_fields = ["id", "created_at"]

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

    class Meta:
        model = IPPool
        fields = ["id", "name", "network_cidr", "gateway", "pool_type", "description", "free_count", "total_count"]

    def get_free_count(self, obj):
        return obj.addresses.filter(status="free").count()

    def get_total_count(self, obj):
        return obj.addresses.count()


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
