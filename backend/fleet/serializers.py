from django.utils import timezone
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
        recorded_at = attrs.get("recorded_at", getattr(self.instance, "recorded_at", None))
        if vehicle is not None and reading_km is not None and recorded_at is not None:
            qs = vehicle.odometer_readings.all()
            if self.instance is not None:
                qs = qs.exclude(pk=self.instance.pk)
            # An odometer only counts up, so a reading is impossible only
            # when it contradicts its neighbours IN TIME -- below what the
            # vehicle already read before it, or above what it read after
            # it. This used to compare against the highest reading ever
            # logged, whenever it was taken, which broke two real jobs. A
            # driver's logbook typed up a week late was refused outright,
            # so the trip could never be recorded. And a slipped digit
            # (450000 for 45000) became permanent: it was the highest
            # reading, so every honest reading afterwards sat below it and
            # was rejected, the vehicle's current km froze at the typo,
            # and the fleet list read Overdue for the rest of its life.
            # __lt, not __lte: a reading stamped at the same instant as an
            # existing one is not "after" it. A vehicle cannot read two
            # different numbers at once, and treating an equal timestamp as
            # later let the higher of the pair straight through -- which is
            # the poisoning route all over again, reachable with the
            # timestamp the log-reading form pre-fills.
            same_instant = qs.filter(recorded_at=recorded_at).exclude(reading_km=reading_km)
            if same_instant.exists():
                raise serializers.ValidationError(
                    f"There is already a reading of {same_instant.first().reading_km} km at exactly "
                    "this time. A vehicle can only read one number at one moment -- correct that "
                    "reading instead, or give this one the time it was actually taken."
                )
            earlier = qs.filter(recorded_at__lt=recorded_at).order_by("-recorded_at", "-id").first()
            if earlier and reading_km < earlier.reading_km:
                raise serializers.ValidationError(
                    f"Odometer reading can't be lower than the {earlier.reading_km} km already "
                    f"recorded on {timezone.localtime(earlier.recorded_at):%Y-%m-%d}."
                )
            later = qs.filter(recorded_at__gt=recorded_at).order_by("recorded_at", "id").first()
            if later and reading_km > later.reading_km:
                raise serializers.ValidationError(
                    f"Odometer reading can't be higher than the {later.reading_km} km recorded "
                    f"later, on {timezone.localtime(later.recorded_at):%Y-%m-%d}."
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
