import datetime

from django.utils import timezone
from rest_framework import permissions, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from accounts.permissions import IsStaffMember, section_permission
from .models import FuelLog, OdometerReading, ServiceRecord, Vehicle
from .serializers import (
    FuelLogSerializer,
    OdometerReadingSerializer,
    ServiceRecordSerializer,
    VehicleSerializer,
)

HasVehiclesAccess = section_permission("vehicles")


def _service_moment(service_date):
    """The instant a service's odometer reading belongs at. Services get
    written up days after the vehicle came back, so a backdated one has to
    be filed at the end of the day it actually happened -- stamped with
    "now" it would be checked against readings taken weeks later, and an
    ordinary late write-up would be refused."""
    if service_date >= timezone.localdate():
        return timezone.now()
    return timezone.make_aware(
        datetime.datetime.combine(service_date, datetime.time.max), timezone.get_current_timezone()
    )


class VehicleViewSet(viewsets.ModelViewSet):
    serializer_class = VehicleSerializer
    permission_classes = [permissions.IsAuthenticated, IsStaffMember, HasVehiclesAccess]
    queryset = (
        Vehicle.objects.select_related("assigned_to")
        .prefetch_related("odometer_readings", "service_records", "fuel_logs")
        .all()
    )
    filterset_fields = ["assigned_to", "is_active"]
    search_fields = ["make", "model", "registration_number"]

    @action(detail=True, methods=["post"])
    def mark_serviced(self, request, pk=None):
        """Logs a ServiceRecord for this vehicle. If the odometer given is
        higher than the latest logged reading, also logs a fresh
        OdometerReading -- so "current km" stays in sync with what was
        actually seen at the workshop, without needing a separate manual
        step. The number is checked against the vehicle's reading history
        first, because a slipped digit here poisons both."""
        vehicle = self.get_object()
        service_date = request.data.get("service_date")
        odometer_km = request.data.get("odometer_km")
        notes = request.data.get("notes", "")

        if not service_date or odometer_km is None:
            return Response({"detail": "service_date and odometer_km are required."}, status=400)
        try:
            service_date = datetime.date.fromisoformat(service_date)
        except (TypeError, ValueError):
            # It used to reach the database as a raw string and blow up
            # with a 500, which tells whoever is on the phone nothing.
            return Response({"detail": "service_date must be a date (YYYY-MM-DD)."}, status=400)
        try:
            odometer_km = int(odometer_km)
        except (TypeError, ValueError):
            return Response({"detail": "odometer_km must be a whole number."}, status=400)
        if odometer_km < 0:
            return Response({"detail": "odometer_km can't be negative."}, status=400)

        # The odometer typed here also lands in the vehicle's reading
        # history, so it gets the same check as a reading logged by hand --
        # which catches a digit slipped DOWNWARDS (4500 for 45000), where
        # the number contradicts a reading already on file.
        #
        # It does NOT catch one slipped upwards. 450000 for 45000 is the
        # newest and highest reading, so nothing on file contradicts it,
        # and no rule here can tell it from a vehicle that genuinely has
        # not been read in a long time without inventing a threshold. An
        # earlier version of this comment claimed the upward slip was
        # prevented. It is not. What is true is that it is now RECOVERABLE:
        # both the reading and the service record can be corrected from the
        # vehicle page.
        #
        # A failed check does not refuse the service. The service happened;
        # refusing to record it strands the vehicle on "In Service" with no
        # way forward, and the odometer is the least important thing on the
        # record. Workshop invoices routinely round -- 45 000 against a
        # driver's 45 123 two days earlier is a rounding, not a lie -- and
        # that used to return 400 and lose the whole service.
        reading = OdometerReadingSerializer(
            data={
                "vehicle": vehicle.pk,
                "reading_km": odometer_km,
                "recorded_at": _service_moment(service_date),
                "notes": "Logged automatically from a service record.",
            }
        )
        # One number IS refusable without inventing a threshold: one that
        # would make the service overdue the moment it is recorded. Next
        # due is odometer + interval, so if that lands behind the km the
        # vehicle has already done, the figure cannot be what the workshop
        # saw -- 4 500 typed for 45 000 on a 10 000 km interval is due at
        # 14 500 against 45 000 already travelled. Recording it would set
        # the bakkie to Overdue the moment it drove out and keep it there.
        # This is derived from the vehicle's own interval rather than a
        # guessed tolerance, which is why it can be a hard refusal.
        if odometer_km + vehicle.service_interval_km <= vehicle.current_odometer_km:
            return Response(
                {"odometer_km": [
                    f"{odometer_km:,} km would put this vehicle's next service at "
                    f"{odometer_km + vehicle.service_interval_km:,} km, which it passed a while ago "
                    f"-- it has already done {vehicle.current_odometer_km:,} km. Check the figure "
                    f"on the invoice."
                ]},
                status=400,
            )

        odometer_warning = None
        if not reading.is_valid():
            odometer_warning = (
                "The service was recorded, but this odometer reading was not added to the "
                "vehicle's history because it contradicts a reading already on file: "
                + " ".join(str(m) for messages in reading.errors.values() for m in messages)
            )

        ServiceRecord.objects.create(
            vehicle=vehicle,
            service_date=service_date,
            odometer_km=odometer_km,
            notes=notes,
            logged_by=request.user,
        )
        if odometer_warning is None and odometer_km > vehicle.current_odometer_km:
            reading.save(recorded_by=request.user)
        # Logging the service IS the vehicle coming back from the workshop,
        # so it comes off "In Service" here rather than needing a second,
        # separate act that somebody will forget. Without this, a vehicle
        # marked in would read "In Service" for the rest of its life and
        # the state would stop meaning anything within a month.
        if vehicle.in_service_since:
            vehicle.in_service_since = None
            vehicle.save(update_fields=["in_service_since"])
        vehicle.refresh_from_db()
        payload = VehicleSerializer(vehicle).data
        if odometer_warning:
            # Surfaced rather than swallowed: the service saved, but the
            # figure on the workshop invoice disagrees with the vehicle's
            # history and somebody has to look at it.
            payload["odometer_warning"] = odometer_warning
        return Response(payload, status=201)

    @action(detail=True, methods=["post"], url_path="book-in")
    def book_in(self, request, pk=None):
        """Mark this vehicle as being at the workshop from a given date
        (defaults to today), or clear it again.

        Send {"in_service_since": null} to take it back out without
        logging a service -- for the booking that got cancelled, which is
        a different thing from the service having happened.
        """
        vehicle = self.get_object()
        if "in_service_since" in request.data:
            value = request.data["in_service_since"]
        else:
            value = timezone.localdate().isoformat()
        serializer = VehicleSerializer(vehicle, data={"in_service_since": value}, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class OdometerReadingViewSet(viewsets.ModelViewSet):
    serializer_class = OdometerReadingSerializer
    permission_classes = [permissions.IsAuthenticated, IsStaffMember, HasVehiclesAccess]
    queryset = OdometerReading.objects.select_related("vehicle", "recorded_by").all()
    filterset_fields = ["vehicle"]

    def perform_create(self, serializer):
        serializer.save(recorded_by=self.request.user)


class ServiceRecordViewSet(viewsets.ModelViewSet):
    serializer_class = ServiceRecordSerializer
    permission_classes = [permissions.IsAuthenticated, IsStaffMember, HasVehiclesAccess]
    queryset = ServiceRecord.objects.select_related("vehicle", "logged_by").all()
    filterset_fields = ["vehicle"]

    def perform_create(self, serializer):
        serializer.save(logged_by=self.request.user)


class FuelLogViewSet(viewsets.ModelViewSet):
    serializer_class = FuelLogSerializer
    permission_classes = [permissions.IsAuthenticated, IsStaffMember, HasVehiclesAccess]
    queryset = FuelLog.objects.select_related("vehicle", "logged_by").all()
    filterset_fields = ["vehicle"]

    def perform_create(self, serializer):
        serializer.save(logged_by=self.request.user)
