from decimal import Decimal

from django.conf import settings
from django.db import models


class Vehicle(models.Model):
    """A company vehicle -- make/model/year plus an optional assigned
    driver. Odometer, service, and fuel history live in the models below,
    keyed off this one; "current km", "next service due", and fuel
    consumption are derived from that history rather than stored here, so
    they can never drift out of sync with it."""

    class FuelType(models.TextChoices):
        PETROL = "petrol", "Petrol"
        DIESEL = "diesel", "Diesel"

    make = models.CharField(max_length=100)
    model = models.CharField(max_length=100)
    year = models.PositiveIntegerField()
    registration_number = models.CharField(max_length=30, unique=True)
    fuel_type = models.CharField(max_length=10, choices=FuelType.choices, default=FuelType.PETROL)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_vehicles",
        limit_choices_to={"role__in": ["admin", "support", "sales", "technician", "management", "accounts"]},
    )
    service_interval_km = models.PositiveIntegerField(
        default=10000, help_text="A service is due every this many kilometers."
    )
    is_active = models.BooleanField(default=True)
    # Set when the vehicle goes into the workshop, cleared when the service
    # is logged. A DATE rather than a boolean because "how long has this
    # been in for a service" is the question that follows immediately, and
    # a flag cannot answer it -- a bakkie that has been off the road for
    # eleven days is a different conversation from one booked in this
    # morning, and a boolean makes those look identical.
    in_service_since = models.DateField(
        null=True, blank=True,
        help_text="Date it went in for a service. Set = at the workshop now. Cleared automatically when a service is logged.",
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["make", "model"]

    def __str__(self):
        return f"{self.year} {self.make} {self.model} ({self.registration_number})"

    @property
    def current_odometer_km(self):
        latest = self.odometer_readings.order_by("-reading_km", "-recorded_at").first()
        return latest.reading_km if latest else 0

    @property
    def last_service(self):
        return self.service_records.order_by("-service_date", "-id").first()

    @property
    def last_service_date(self):
        service = self.last_service
        return service.service_date if service else None

    @property
    def last_service_km(self):
        service = self.last_service
        return service.odometer_km if service else 0

    @property
    def next_service_due_km(self):
        return self.last_service_km + self.service_interval_km

    @property
    def km_until_due(self):
        return self.next_service_due_km - self.current_odometer_km

    @property
    def service_status(self):
        """"in_service" while it's at the workshop, otherwise "overdue"
        once past the due km, "due_soon" inside a warning band before it
        (10% of the interval, or 500km, whichever is bigger -- so short
        intervals still get a sensible heads-up window), else "ok".

        Being at the workshop OVERRIDES the km-derived states, and that
        ordering is the point of the state existing. A bakkie booked in
        this morning is still 2,000km past its due mark, so the derived
        answer is "overdue" -- which reads as nobody having dealt with it,
        when somebody is dealing with it right now. Whoever is scanning
        this list is deciding what to chase, and a vehicle already at the
        workshop is the one thing on it that needs no chasing.
        """
        if self.in_service_since:
            return "in_service"
        km_until = self.km_until_due
        if km_until <= 0:
            return "overdue"
        warning_band = max(int(self.service_interval_km * 0.1), 500)
        if km_until <= warning_band:
            return "due_soon"
        return "ok"

    @property
    def days_in_service(self):
        """How long it has been at the workshop, or None if it isn't."""
        if not self.in_service_since:
            return None
        from django.utils import timezone

        return max(0, (timezone.localdate() - self.in_service_since).days)

    @property
    def total_litres_used(self):
        total = self.fuel_logs.aggregate(total=models.Sum("litres"))["total"]
        return total or Decimal("0")

    @property
    def average_km_per_litre(self):
        """Full-to-full method: total distance covered between the
        earliest and latest fill-up, divided by all litres put in since
        (excluding the earliest log's litres, since those fuelled the
        vehicle before the tracked distance began). Needs at least two
        fuel logs to mean anything."""
        logs = list(self.fuel_logs.order_by("odometer_km", "filled_at"))
        if len(logs) < 2:
            return None
        distance = logs[-1].odometer_km - logs[0].odometer_km
        litres = sum((log.litres for log in logs[1:]), Decimal("0"))
        if distance <= 0 or litres <= 0:
            return None
        return round(distance / float(litres), 2)


class OdometerReading(models.Model):
    """One logged odometer reading for a vehicle. The vehicle's "current
    km" is always its latest reading by value -- readings shouldn't
    decrease over the vehicle's life (enforced in the serializer, not
    here, to give a clean API error rather than a 500)."""

    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE, related_name="odometer_readings")
    reading_km = models.PositiveIntegerField()
    recorded_at = models.DateTimeField()
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    notes = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-recorded_at", "-id"]

    def __str__(self):
        return f"{self.vehicle} — {self.reading_km} km"


class ServiceRecord(models.Model):
    """A logged service event -- resets "next service due" from this
    point (last_service_km + service_interval_km on the Vehicle)."""

    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE, related_name="service_records")
    service_date = models.DateField()
    odometer_km = models.PositiveIntegerField()
    notes = models.TextField(blank=True)
    logged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-service_date", "-id"]

    def __str__(self):
        return f"{self.vehicle} serviced {self.service_date}"


class FuelLog(models.Model):
    """One fuel fill-up for a vehicle -- litres added plus the odometer
    reading at the time, so average consumption can be derived using the
    standard full-to-full method (see Vehicle.average_km_per_litre)."""

    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE, related_name="fuel_logs")
    filled_at = models.DateField()
    litres = models.DecimalField(max_digits=7, decimal_places=2)
    odometer_km = models.PositiveIntegerField()
    notes = models.CharField(max_length=255, blank=True)
    logged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-filled_at", "-id"]

    def __str__(self):
        return f"{self.vehicle} — {self.litres} L on {self.filled_at}"
