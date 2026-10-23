"""Licence renewals -- the vehicle disc and the driver's card.

What is pinned here is not "does the date arithmetic work". It is the
handful of ways a renewal reminder silently stops reminding anybody:

A MISSING DATE MUST NOT READ AS COMPLIANT. A vehicle nobody has captured
a disc date for is unaccounted for, not fine. If `unknown` folded into
`ok` the feature would ship looking perfectly healthy on day one, with
every vehicle on the platform hidden from the list it exists to populate.

EXPIRING TODAY IS NOT EXPIRED. A disc is valid through the last day
printed on it. Off by one here puts a legal vehicle on the illegal list,
and a list that cries wolf gets ignored.

RENEWING EARLY MUST NOT COST THE EARLY DAYS. Counting a new year from
today rather than from the old expiry throws away however long was left,
every single cycle -- and early is when people actually renew.

A SLIPPED YEAR IS THE ONE TYPO NOTHING ELSE CATCHES. 2036 for 2026 is a
valid date that sorts to the bottom and removes the vehicle from the list
permanently. Nothing else on the screen would ever contradict it.

THE TILE AND THE TAB MUST COUNT THE SAME THING. They are fed by one
endpoint for that reason, and the count is asserted against the rows.
"""
import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from fleet import renewals
from fleet.models import Vehicle
from payroll.models import StaffProfile

User = get_user_model()

# A fixed date for the pure-arithmetic tests. The endpoint tests use the
# real today, because what they are checking is that the API agrees with
# the clock the page is read against.
ANCHOR = datetime.date(2026, 9, 17)


class RenewalRuleTests(TestCase):
    """The shared arithmetic, tested directly -- both apps call it."""

    def test_no_date_is_unknown_not_ok(self):
        self.assertEqual(renewals.renewal_status(None, 30, ANCHOR), renewals.UNKNOWN)

    def test_the_last_day_printed_on_the_disc_is_still_valid(self):
        self.assertEqual(renewals.renewal_status(ANCHOR, 30, ANCHOR), renewals.DUE_SOON)
        self.assertEqual(renewals.days_until(ANCHOR, ANCHOR), 0)

    def test_the_day_after_is_expired(self):
        yesterday = ANCHOR - datetime.timedelta(days=1)

        self.assertEqual(renewals.renewal_status(yesterday, 30, ANCHOR), renewals.EXPIRED)
        self.assertEqual(renewals.days_until(yesterday, ANCHOR), -1)

    def test_the_notice_window_boundary_is_inclusive(self):
        edge = ANCHOR + datetime.timedelta(days=30)
        beyond = ANCHOR + datetime.timedelta(days=31)

        self.assertEqual(renewals.renewal_status(edge, 30, ANCHOR), renewals.DUE_SOON)
        self.assertEqual(renewals.renewal_status(beyond, 30, ANCHOR), renewals.OK)

    def test_a_driver_licence_gets_a_wider_window_than_a_disc(self):
        """The same date is urgent for a card and not yet for a disc,
        because a card needs a testing-centre booking and a disc does not.
        If these ever collapse to one number, the wider case is the one
        that breaks, silently."""
        in_45_days = ANCHOR + datetime.timedelta(days=45)

        self.assertEqual(renewals.renewal_status(in_45_days, 30, ANCHOR), renewals.OK)
        self.assertEqual(renewals.renewal_status(in_45_days, 60, ANCHOR), renewals.DUE_SOON)

    def test_the_two_windows_really_are_different(self):
        """Guards the constants themselves, not just the function. Setting
        them equal would quietly undo the paragraph above."""
        self.assertEqual(Vehicle.RENEWAL_NOTICE_DAYS, 30)
        self.assertEqual(StaffProfile.RENEWAL_NOTICE_DAYS, 60)

    def test_renewing_early_extends_from_the_old_expiry(self):
        expiry = ANCHOR + datetime.timedelta(days=21)

        self.assertEqual(renewals.next_expiry(expiry, 1, ANCHOR), datetime.date(2027, 10, 8))

    def test_renewing_late_counts_from_today(self):
        """There is nothing left to extend, so counting from the lapsed
        date would hand back another date in the past."""
        lapsed = datetime.date(2025, 3, 1)

        self.assertEqual(renewals.next_expiry(lapsed, 1, ANCHOR), datetime.date(2027, 9, 17))

    def test_a_first_capture_counts_from_today(self):
        self.assertEqual(renewals.next_expiry(None, 5, ANCHOR), datetime.date(2031, 9, 17))

    def test_the_29th_of_february_does_not_explode(self):
        self.assertEqual(
            renewals.add_years(datetime.date(2028, 2, 29), 1), datetime.date(2029, 2, 28)
        )

    def test_a_slipped_year_is_refused(self):
        self.assertIsNotNone(renewals.expiry_is_sane(datetime.date(2036, 9, 17), 1, ANCHOR))

    def test_a_date_in_the_past_is_allowed(self):
        """Capturing a disc that already lapsed is a real thing to do, and
        the row honestly reading "expired" is the point of capturing it."""
        self.assertIsNone(renewals.expiry_is_sane(datetime.date(2025, 1, 1), 1, ANCHOR))

    def test_ordering_puts_unknown_above_ok(self):
        """A date nobody has captured could already have passed. It
        outranks a disc that is good for another eight months."""
        unknown = renewals.sort_key(renewals.UNKNOWN, None, None)
        fine = renewals.sort_key(renewals.OK, ANCHOR + datetime.timedelta(days=240), 240)

        self.assertLess(unknown, fine)


class VehicleLicenceTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.vehicle = Vehicle.objects.create(
            make="Toyota", model="Hilux", year=2023, registration_number="KDS114FS",
        )

    def test_a_vehicle_with_no_date_is_unknown(self):
        self.assertEqual(self.vehicle.licence_status, renewals.UNKNOWN)
        self.assertIsNone(self.vehicle.licence_days_left)

    def test_status_follows_the_captured_date(self):
        self.vehicle.licence_expiry = self.today + datetime.timedelta(days=10)
        self.assertEqual(self.vehicle.licence_status, renewals.DUE_SOON)
        self.assertEqual(self.vehicle.licence_days_left, 10)

        self.vehicle.licence_expiry = self.today - datetime.timedelta(days=2)
        self.assertEqual(self.vehicle.licence_status, renewals.EXPIRED)

        self.vehicle.licence_expiry = self.today + datetime.timedelta(days=200)
        self.assertEqual(self.vehicle.licence_status, renewals.OK)


class RenewalsEndpointTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.client = APIClient()
        self.client.force_authenticate(
            User.objects.create_user(username="boss", password="x", role="admin", is_staff=True)
        )

        self.driver = User.objects.create_user(
            username="chris", password="x", role="technician", is_staff=True,
            first_name="Chris", last_name="Visser",
        )
        self.profile = StaffProfile.objects.create(
            user=self.driver, pay_type=StaffProfile.PayType.HOURLY, hourly_rate="120.00",
            license_number="1234567890",
            license_expiry=self.today + datetime.timedelta(days=20),
        )
        self.expired = Vehicle.objects.create(
            make="Ford", model="Ranger", year=2021, registration_number="AAA111FS",
            licence_expiry=self.today - datetime.timedelta(days=5),
        )
        self.due = Vehicle.objects.create(
            make="VW", model="Polo", year=2022, registration_number="BBB222FS",
            licence_expiry=self.today + datetime.timedelta(days=12),
            assigned_to=self.driver,
        )
        self.fine = Vehicle.objects.create(
            make="Isuzu", model="D-Max", year=2024, registration_number="CCC333FS",
            licence_expiry=self.today + datetime.timedelta(days=300),
        )
        self.unknown = Vehicle.objects.create(
            make="Nissan", model="NP200", year=2020, registration_number="DDD444FS",
        )

    def _renewals(self):
        response = self.client.get("/api/vehicles/renewals/")
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_rows_are_ordered_worst_first(self):
        statuses = [row["status"] for row in self._renewals()["results"]]

        self.assertEqual(statuses[0], renewals.EXPIRED)
        # A monotonic walk down the bands, never back up.
        ranks = [renewals.STATUS_ORDER[s] for s in statuses]
        self.assertEqual(ranks, sorted(ranks))

    def test_the_count_matches_the_rows_it_is_counted_from(self):
        """The dashboard tile shows `action_needed` and the tab shows the
        rows. They come from one endpoint precisely so they cannot drift;
        this is the assertion that keeps that true."""
        data = self._renewals()
        needing = [r for r in data["results"] if r["status"] in renewals.ACTION_NEEDED]

        self.assertEqual(data["counts"]["action_needed"], len(needing))
        self.assertEqual(data["counts"]["expired"], 1)
        self.assertEqual(data["counts"]["unknown"], 1)

    def test_a_vehicle_with_no_date_still_appears(self):
        keys = {row["key"] for row in self._renewals()["results"]}

        self.assertIn(f"vehicle-{self.unknown.pk}", keys)

    def test_the_driver_licence_appears_beside_the_discs(self):
        driver_rows = [r for r in self._renewals()["results"] if r["kind"] == "driver_licence"]

        self.assertEqual(len(driver_rows), 1)
        self.assertEqual(driver_rows[0]["title"], "Chris Visser")
        self.assertEqual(driver_rows[0]["notice_days"], StaffProfile.RENEWAL_NOTICE_DAYS)

    def test_office_staff_who_do_not_drive_are_not_on_the_list(self):
        """A list that is mostly people who will never touch a bakkie is a
        list that gets skimmed."""
        clerk = User.objects.create_user(
            username="clerk", password="x", role="accounts", is_staff=True
        )
        StaffProfile.objects.create(
            user=clerk, pay_type=StaffProfile.PayType.SALARY, monthly_salary="20000.00"
        )

        titles = {r["title"] for r in self._renewals()["results"] if r["kind"] == "driver_licence"}

        self.assertNotIn("clerk", titles)

    def test_a_driver_with_a_vehicle_and_nothing_captured_does_appear(self):
        """The important half of that filter. Somebody assigned a vehicle
        with no licence on file is precisely the person to chase -- if only
        captured data qualified, the unchecked people would be the ones who
        never show up."""
        hand = User.objects.create_user(
            username="sipho", password="x", role="technician", is_staff=True, first_name="Sipho"
        )
        StaffProfile.objects.create(
            user=hand, pay_type=StaffProfile.PayType.HOURLY, hourly_rate="90.00"
        )
        Vehicle.objects.create(
            make="Toyota", model="Quantum", year=2019, registration_number="EEE555FS",
            assigned_to=hand,
        )

        rows = [r for r in self._renewals()["results"] if r["kind"] == "driver_licence"]
        sipho = [r for r in rows if r["title"] == "Sipho"]

        self.assertEqual(len(sipho), 1)
        self.assertEqual(sipho[0]["status"], renewals.UNKNOWN)

    def test_inactive_vehicles_are_left_off(self):
        self.fine.is_active = False
        self.fine.save(update_fields=["is_active"])

        keys = {row["key"] for row in self._renewals()["results"]}

        self.assertNotIn(f"vehicle-{self.fine.pk}", keys)


class RenewActionTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.client = APIClient()
        self.client.force_authenticate(
            User.objects.create_user(username="boss", password="x", role="admin", is_staff=True)
        )
        self.vehicle = Vehicle.objects.create(
            make="Ford", model="Ranger", year=2021, registration_number="AAA111FS",
            licence_expiry=self.today + datetime.timedelta(days=21),
        )

    def test_renewing_without_a_date_extends_from_the_old_expiry(self):
        response = self.client.post(f"/api/vehicles/{self.vehicle.pk}/renew-licence/", {})

        self.assertEqual(response.status_code, 200, response.data)
        self.vehicle.refresh_from_db()
        self.assertEqual(
            self.vehicle.licence_expiry,
            renewals.add_years(self.today + datetime.timedelta(days=21), 1),
        )

    def test_an_explicit_date_wins(self):
        wanted = (self.today + datetime.timedelta(days=200)).isoformat()

        response = self.client.post(
            f"/api/vehicles/{self.vehicle.pk}/renew-licence/", {"licence_expiry": wanted}
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.vehicle.refresh_from_db()
        self.assertEqual(self.vehicle.licence_expiry.isoformat(), wanted)

    def test_a_slipped_year_is_refused_by_the_endpoint(self):
        response = self.client.post(
            f"/api/vehicles/{self.vehicle.pk}/renew-licence/",
            {"licence_expiry": renewals.add_years(self.today, 10).isoformat()},
        )

        self.assertEqual(response.status_code, 400)
        self.vehicle.refresh_from_db()
        self.assertEqual(self.vehicle.licence_expiry, self.today + datetime.timedelta(days=21))

    def test_rubbish_gets_a_message_not_a_500(self):
        response = self.client.post(
            f"/api/vehicles/{self.vehicle.pk}/renew-licence/", {"licence_expiry": "next tuesday"}
        )

        self.assertEqual(response.status_code, 400)

    def test_renewing_a_driver_licence_defaults_to_five_years(self):
        driver = User.objects.create_user(
            username="chris", password="x", role="technician", is_staff=True
        )
        profile = StaffProfile.objects.create(
            user=driver, pay_type=StaffProfile.PayType.HOURLY, hourly_rate="120.00",
            license_number="1234567890",
        )

        response = self.client.post(
            "/api/vehicles/renew-driver-licence/", {"staff_profile": profile.pk}
        )

        self.assertEqual(response.status_code, 200, response.data)
        profile.refresh_from_db()
        self.assertEqual(profile.license_expiry, renewals.add_years(self.today, 5))

    def test_renewing_a_driver_licence_touches_only_that_date(self):
        """This endpoint sits under the vehicles section, so it must not be
        a side door into a payroll record."""
        driver = User.objects.create_user(
            username="chris", password="x", role="technician", is_staff=True
        )
        profile = StaffProfile.objects.create(
            user=driver, pay_type=StaffProfile.PayType.HOURLY, hourly_rate="120.00",
        )

        response = self.client.post(
            "/api/vehicles/renew-driver-licence/",
            {"staff_profile": profile.pk, "hourly_rate": "9999.00", "annual_leave_balance": "99"},
        )

        self.assertEqual(response.status_code, 200, response.data)
        profile.refresh_from_db()
        self.assertEqual(str(profile.hourly_rate), "120.00")
        self.assertEqual(str(profile.annual_leave_balance), "21.0")

    def test_an_unknown_staff_member_is_a_400_not_a_500(self):
        response = self.client.post(
            "/api/vehicles/renew-driver-licence/", {"staff_profile": "banana"}
        )

        self.assertEqual(response.status_code, 400)


class LicenceCaptureTests(TestCase):
    """The date is also editable straight off the vehicle's Edit form."""

    def setUp(self):
        self.today = timezone.localdate()
        self.client = APIClient()
        self.client.force_authenticate(
            User.objects.create_user(username="boss", password="x", role="admin", is_staff=True)
        )
        self.vehicle = Vehicle.objects.create(
            make="Ford", model="Ranger", year=2021, registration_number="AAA111FS",
        )

    def test_patching_the_date_works(self):
        wanted = (self.today + datetime.timedelta(days=90)).isoformat()

        response = self.client.patch(
            f"/api/vehicles/{self.vehicle.pk}/", {"licence_expiry": wanted}, format="json"
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["licence_expiry"], wanted)
        self.assertEqual(response.data["licence_status"], renewals.OK)
        self.assertEqual(response.data["licence_days_left"], 90)

    def test_patching_a_slipped_year_is_refused(self):
        response = self.client.patch(
            f"/api/vehicles/{self.vehicle.pk}/",
            {"licence_expiry": renewals.add_years(self.today, 10).isoformat()},
            format="json",
        )

        self.assertEqual(response.status_code, 400)

    def test_clearing_the_date_is_allowed(self):
        """Capturing the wrong vehicle's disc has to be undoable, and back
        to `unknown` is the honest state -- not whatever was typed."""
        self.vehicle.licence_expiry = self.today
        self.vehicle.save(update_fields=["licence_expiry"])

        response = self.client.patch(
            f"/api/vehicles/{self.vehicle.pk}/", {"licence_expiry": None}, format="json"
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNone(response.data["licence_expiry"])
        self.assertEqual(response.data["licence_status"], renewals.UNKNOWN)
