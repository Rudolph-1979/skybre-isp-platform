"""A vehicle that is at the workshop right now.

The state exists because the derived one lies about it. service_status is
worked out from odometer versus interval, so a bakkie booked in this
morning is still 2,000km past its due mark and still reads "Overdue" --
which, to whoever is scanning the list deciding what to chase, means
nobody has dealt with it. Somebody is dealing with it right now.
"""
import datetime

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from fleet.models import OdometerReading, Vehicle


class InServiceTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.staff = User.objects.create_user(username="fleet", password="x", role=User.Role.ADMIN)
        self.vehicle = Vehicle.objects.create(
            make="Toyota", model="Hilux", year=2022,
            registration_number="ABC123GP", service_interval_km=10000,
        )

    def _drive(self, km):
        OdometerReading.objects.create(
            vehicle=self.vehicle, reading_km=km, recorded_at=timezone.now(), recorded_by=self.staff
        )

    def test_a_vehicle_not_at_the_workshop_keeps_its_derived_status(self):
        self._drive(12000)
        self.assertEqual(self.vehicle.service_status, "overdue")

    def test_being_at_the_workshop_overrides_overdue(self):
        """The case the whole state exists for. Still 2,000km past due,
        but it is being dealt with, so it must not sit in the chase pile."""
        self._drive(12000)
        self.vehicle.in_service_since = timezone.localdate()
        self.vehicle.save()
        self.assertEqual(self.vehicle.service_status, "in_service")

    def test_it_overrides_due_soon_and_ok_too(self):
        self._drive(500)
        self.assertEqual(self.vehicle.service_status, "ok")
        self.vehicle.in_service_since = timezone.localdate()
        self.vehicle.save()
        self.assertEqual(self.vehicle.service_status, "in_service")

    def test_days_in_service_counts_from_the_booking_date(self):
        """A flag would say it's at the workshop. The count says whether
        that is still reasonable."""
        self.vehicle.in_service_since = timezone.localdate() - datetime.timedelta(days=11)
        self.vehicle.save()
        self.assertEqual(self.vehicle.days_in_service, 11)

    def test_days_in_service_is_none_when_it_is_not_there(self):
        self.assertIsNone(self.vehicle.days_in_service)

    def test_booked_in_today_reads_zero_days_not_none(self):
        """0 and "not there at all" are different answers and must not
        collapse into the same falsy value on screen."""
        self.vehicle.in_service_since = timezone.localdate()
        self.vehicle.save()
        self.assertEqual(self.vehicle.days_in_service, 0)

    # --- coming back out -------------------------------------------------

    def test_logging_the_service_brings_it_back_out(self):
        """Without this, a vehicle marked in reads "In service" for the
        rest of its life and the state stops meaning anything inside a
        month."""
        self._drive(12000)
        self.vehicle.in_service_since = timezone.localdate()
        self.vehicle.save()

        self.client.force_authenticate(self.staff)
        res = self.client.post(
            f"/api/vehicles/{self.vehicle.pk}/mark_serviced/",
            {"service_date": timezone.localdate().isoformat(), "odometer_km": 12050},
            format="json",
        )
        self.assertEqual(res.status_code, 201)
        self.vehicle.refresh_from_db()
        self.assertIsNone(self.vehicle.in_service_since)
        # And the km countdown restarted, so it is no longer overdue.
        self.assertEqual(self.vehicle.service_status, "ok")

    def test_it_can_be_taken_back_out_without_logging_a_service(self):
        """A cancelled booking is not the same event as a completed
        service, and only the second should reset the km countdown."""
        self._drive(12000)
        self.vehicle.in_service_since = timezone.localdate()
        self.vehicle.save()

        self.client.force_authenticate(self.staff)
        res = self.client.post(
            f"/api/vehicles/{self.vehicle.pk}/book-in/", {"in_service_since": None}, format="json"
        )
        self.assertEqual(res.status_code, 200)
        self.vehicle.refresh_from_db()
        self.assertIsNone(self.vehicle.in_service_since)
        # Still overdue -- nothing was actually serviced.
        self.assertEqual(self.vehicle.service_status, "overdue")

    def test_booking_in_defaults_to_today(self):
        self.client.force_authenticate(self.staff)
        res = self.client.post(f"/api/vehicles/{self.vehicle.pk}/book-in/", {}, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["in_service_since"], timezone.localdate().isoformat())
        self.assertEqual(res.data["service_status"], "in_service")

    def test_the_api_reports_the_new_status_and_the_day_count(self):
        self.vehicle.in_service_since = timezone.localdate() - datetime.timedelta(days=3)
        self.vehicle.save()
        self.client.force_authenticate(self.staff)
        res = self.client.get(f"/api/vehicles/{self.vehicle.pk}/")
        self.assertEqual(res.data["service_status"], "in_service")
        self.assertEqual(res.data["days_in_service"], 3)

    def test_it_needs_vehicles_access(self):
        desk = User.objects.create_user(
            username="desk", password="x", role=User.Role.SUPPORT, allowed_sections=["customers"]
        )
        self.client.force_authenticate(desk)
        res = self.client.post(f"/api/vehicles/{self.vehicle.pk}/book-in/", {}, format="json")
        self.assertEqual(res.status_code, 403)
