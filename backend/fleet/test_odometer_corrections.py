"""Odometer readings that have to be fixable after the fact.

Two real jobs used to be impossible on this screen. A driver's logbook is
typed up days after the trip, and a backdated reading was refused for
being lower than the newest one. And an odometer keyed in wrong at "Mark
as serviced" -- 450 000 for 45 000, or 4 500 -- was permanent: the typo
became the vehicle's "current km", every honest reading after it was
rejected for being lower, and the bakkie sat on the fleet list reading
Overdue for the rest of its life.
"""
import datetime

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from fleet.models import OdometerReading, ServiceRecord, Vehicle


class OdometerCorrectionTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.staff = User.objects.create_user(username="fleet", password="x", role=User.Role.ADMIN)
        self.client.force_authenticate(self.staff)
        self.vehicle = Vehicle.objects.create(
            make="Toyota", model="Hilux", year=2022,
            registration_number="ABC123GP", service_interval_km=10000,
        )

    def _reading(self, km, days_ago=0):
        return OdometerReading.objects.create(
            vehicle=self.vehicle, reading_km=km, recorded_by=self.staff,
            recorded_at=timezone.now() - datetime.timedelta(days=days_ago),
        )

    def _mark_serviced(self, service_date, odometer_km):
        return self.client.post(
            f"/api/vehicles/{self.vehicle.pk}/mark_serviced/",
            {"service_date": str(service_date), "odometer_km": odometer_km},
            format="json",
        )

    def _log(self, km, days_ago=0):
        return self.client.post(
            "/api/odometer-readings/",
            {
                "vehicle": self.vehicle.pk,
                "reading_km": km,
                "recorded_at": (timezone.now() - datetime.timedelta(days=days_ago)).isoformat(),
            },
            format="json",
        )

    def test_a_backdated_reading_is_still_accepted(self):
        """Driver logbooks are typed up days late. The entry for last
        Tuesday sits below today's reading and must still go in, or the
        trip can never be recorded at all."""
        self._reading(45000)
        res = self._log(44300, days_ago=7)
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(self.vehicle.current_odometer_km, 45000)

    def test_a_backdated_reading_above_a_later_one_is_refused(self):
        """An odometer counts up. 46 000 last week and 45 000 today cannot
        both be true, and letting it in would freeze the vehicle's current
        km above reality -- which is exactly how the old typo stuck."""
        self._reading(45000)
        res = self._log(46000, days_ago=7)
        self.assertEqual(res.status_code, 400)

    def test_a_mistyped_service_odometer_is_refused_instead_of_recorded(self):
        """4 500 for 45 000 at the workshop used to be accepted, and it
        reset the km countdown from a number the vehicle passed years ago
        -- so the bakkie read Overdue the moment it came back out, for
        good. Nothing may be written when the number is impossible."""
        self._reading(45000)
        res = self.client.post(
            f"/api/vehicles/{self.vehicle.pk}/mark_serviced/",
            {"service_date": timezone.localdate().isoformat(), "odometer_km": 4500},
            format="json",
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.vehicle.service_records.count(), 0)
        self.assertEqual(self.vehicle.service_status, "overdue")

    def test_a_service_written_up_late_files_its_reading_on_the_day_it_happened(self):
        """Services get written up after the fact. Stamping the reading
        with "now" puts it in the wrong place in the odometer history, and
        then honest readings taken between the service and the write-up
        look like they go backwards."""
        self._reading(43000, days_ago=40)
        service_date = timezone.localdate() - datetime.timedelta(days=20)
        res = self.client.post(
            f"/api/vehicles/{self.vehicle.pk}/mark_serviced/",
            {"service_date": service_date.isoformat(), "odometer_km": 44000},
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.data)
        logged = self.vehicle.odometer_readings.order_by("-reading_km").first()
        self.assertEqual(logged.reading_km, 44000)
        self.assertEqual(timezone.localtime(logged.recorded_at).date(), service_date)

    def test_a_service_date_that_is_not_a_date_is_a_clean_error(self):
        """It used to reach the database as a string and blow up with a
        500, which tells whoever is on the phone nothing."""
        res = self.client.post(
            f"/api/vehicles/{self.vehicle.pk}/mark_serviced/",
            {"service_date": "12/03/2026", "odometer_km": 45000},
            format="json",
        )
        self.assertEqual(res.status_code, 400)

    # ---- a rounded workshop figure must not lose the whole service -------

    def test_a_workshop_figure_below_a_recent_reading_still_records_the_service(self):
        """Workshop invoices round. 45 000 against a driver's 45 123 logged
        two days earlier is a rounding, not a lie.

        The first version of this checked the odometer BEFORE writing the
        service and returned 400 on a failed check, so the service was lost
        entirely and the vehicle sat on "In Service" with no way forward.
        The service happened; the odometer is the least important thing on
        the record.
        """
        self._reading(45123, days_ago=2)
        self.vehicle.in_service_since = timezone.localdate()
        self.vehicle.save(update_fields=["in_service_since"])

        response = self._mark_serviced(timezone.localdate(), 45000)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(ServiceRecord.objects.filter(vehicle=self.vehicle).exists(),
                        "the service must be recorded even when its odometer is questioned")
        self.vehicle.refresh_from_db()
        self.assertIsNone(self.vehicle.in_service_since,
                          "the vehicle must come off In Service when it comes back")

    def test_and_it_says_the_reading_was_not_added(self):
        """Silently dropping the reading would be its own trap -- staff have
        to know the history was not updated."""
        self._reading(45123, days_ago=2)
        response = self._mark_serviced(timezone.localdate(), 45000)
        self.assertIn("odometer_warning", response.data)
        self.assertEqual(
            OdometerReading.objects.filter(vehicle=self.vehicle, reading_km=45000).count(), 0,
            "the contradictory reading must not enter the history",
        )

    def test_a_sensible_service_odometer_is_still_logged_as_a_reading(self):
        """The ordinary case must keep working: a higher figure at the
        workshop becomes the vehicle's current km."""
        self._reading(45000, days_ago=2)
        response = self._mark_serviced(timezone.localdate(), 46200)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertNotIn("odometer_warning", response.data)
        self.vehicle.refresh_from_db()
        self.assertEqual(self.vehicle.current_odometer_km, 46200)

