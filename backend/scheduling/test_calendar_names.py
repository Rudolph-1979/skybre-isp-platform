"""Calendar chips name the person, not the login.

`assigned_to_name` and `staff_name` serialised `user.username`, which on
this platform is a login -- "cbosman", "admin2". The Scheduling page's
own staff dropdowns have always shown `first_name || username`, so the
same technician appeared one way in the filter and another way on the
calendar, and the month grid did not show the assignee at all.

The rule now lives in scheduling.serializers.staff_display_name, once,
and these pin both ends of it: the friendly name when there is one, and
the username when there is not -- because a blank chip would be worse
than a login name.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from customers.models import Customer
from scheduling.models import Job, Shift

User = get_user_model()


class CalendarNameTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="boss", password="x", role="admin", is_staff=True,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

        self.named = User.objects.create_user(
            username="cbosman", password="x", role="technician",
            first_name="Christo", last_name="Bosman",
        )
        self.nameless = User.objects.create_user(
            username="zandre", password="x", role="technician",
        )
        self.customer = Customer.objects.create(full_name="Lucille Slabbert", email="l@x.com")
        self.start = timezone.now().replace(microsecond=0)

    def _job(self, assigned_to):
        return Job.objects.create(
            customer=self.customer, assigned_to=assigned_to, title="Test",
            start=self.start, end=self.start + timedelta(hours=1),
        )

    def _row(self, job):
        return self.client.get(f"/api/jobs/{job.pk}/").json()

    def test_a_job_reports_the_technicians_first_name(self):
        job = self._job(self.named)

        self.assertEqual(self._row(job)["assigned_to_name"], "Christo")

    def test_a_staff_member_with_no_first_name_falls_back_to_the_username(self):
        """Better a login than a blank chip: the calendar still has to say
        somebody is on it."""
        job = self._job(self.nameless)

        self.assertEqual(self._row(job)["assigned_to_name"], "zandre")

    def test_an_unassigned_job_reports_nothing_rather_than_a_placeholder(self):
        """The API says there is no assignee; the CALENDAR decides that reads
        as "Unassigned". A server-side placeholder would be a name that
        cannot be told apart from a real one by anything consuming this."""
        job = self._job(None)

        self.assertIsNone(self._row(job)["assigned_to_name"])

    def test_a_shift_names_the_person_too(self):
        shift = Shift.objects.create(
            staff=self.named, start=self.start, end=self.start + timedelta(hours=8),
        )

        row = self.client.get(f"/api/shifts/{shift.pk}/").json()

        self.assertEqual(row["staff_name"], "Christo")

    def test_whitespace_only_first_name_is_treated_as_absent(self):
        """An imported staff row with " " in the column would otherwise put
        an invisible name on every chip that person is on."""
        self.nameless.first_name = "   "
        self.nameless.save(update_fields=["first_name"])

        job = self._job(self.nameless)

        self.assertEqual(self._row(job)["assigned_to_name"], "zandre")
