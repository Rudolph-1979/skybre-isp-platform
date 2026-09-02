from rest_framework import serializers

from .models import FuelLog, OdometerReading, ServiceRecord, Vehicle


def _display_name(user):
    if not user:
        return None
    return user.get_full_name() or user.username


class OdometerReadingSerializer(serializers.ModelSerializer):
    recorded_by_name = serializers.SerializerMethodField()

    class Meta:
        model = OdometerReading
        fields = [
            "id", "vehicle", "reading_km", "recorded_at", "recorded_by",
            "recorded_by_name", "notes", "created_at",
        ]
        read_only_fields = ["id", "recorded_by", "created_at"]

    def get_recorded_by_name(self, obj):
        return _display_name(obj.recorded_by)

    def validate(self, attrs):
        vehicle = attrs.get("vehicle") or getattr(self.instance, "vehicle", None)
        reading_km = attrs.get("reading_km", getattr(self.instance, "reading_km", None))
        if vehicle is not None and reading_km is not None:
            qs = vehicle.odometer_readings.all()
            if self.instance is not None:
                qs = qs.exclude(pk=self.instance.pk)
            latest = qs.order_by("-reading_km").first()
            if latest and reading_km < latest.reading_km:
                raise serializers.ValidationError(
                    f"Odometer reading can't be lower than the last recorded reading ({latest.reading_km} km)."
                )
        return attrs


class ServiceRecordSerializer(serializers.ModelSerializer):
    logged_by_name = serializers.SerializerMethodField()

    class Meta:
        model = ServiceRecord
        fields = [
            "id", "vehicle", "service_date", "odometer_km", "notes",
            "logged_by", "logged_by_name", "created_at",
        ]
        read_only_fields = ["id", "logged_by", "created_at"]

    def get_logged_by_name(self, obj):
        return _display_name(obj.logged_by)


class FuelLogSerializer(serializers.ModelSerializer):
    logged_by_name = serializers.SerializerMethodField()

    class Meta:
        model = FuelLog
        fields = [
            "id", "vehicle", "filled_at", "litres", "odometer_km", "notes",
            "logged_by", "logged_by_name", "created_at",
        ]
        read_only_fields = ["id", "logged_by", "created_at"]

    def get_logged_by_name(self, obj):
        return _display_name(obj.logged_by)


class VehicleSerializer(serializers.ModelSerializer):
    assigned_to_name = serializers.SerializerMethodField()
    current_odometer_km = serializers.IntegerField(read_only=True)
    last_service_date = serializers.DateField(read_only=True)
    last_service_km = serializers.IntegerField(read_only=True)
    next_service_due_km = serializers.IntegerField(read_only=True)
    km_until_due = serializers.IntegerField(read_only=True)
    service_status = serializers.CharField(read_only=True)
    # How long it has been at the workshop. Null when it isn't there.
    days_in_service = serializers.IntegerField(read_only=True, allow_null=True)
    total_litres_used = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    average_km_per_litre = serializers.FloatField(read_only=True)

    class Meta:
        model = Vehicle
        fields = [
            "id", "make", "model", "year", "registration_number", "fuel_type",
            "assigned_to", "assigned_to_name", "service_interval_km",
            "is_active", "in_service_since", "days_in_service", "notes", "created_at",
            "current_odometer_km", "last_service_date", "last_service_km",
            "next_service_due_km", "km_until_due", "service_status",
            "total_litres_used", "average_km_per_litre",
        ]
        read_only_fields = ["id", "created_at"]

    def get_assigned_to_name(self, obj):
        return _display_name(obj.assigned_to)
