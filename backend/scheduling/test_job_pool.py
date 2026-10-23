"""The Job pool -- Rudolph, 30 Sep: "create a Job pool where jobs land
before scheduled... Jobs can then be pulled from the pool to better
schedule route and times."

A pool job is exactly a Job whose start (and end) are blank -- there is
no separate flag. JobSerializer.validate is what makes that a coherent
state rather than an accident: start and end always move together, and a
pool job can never carry assigned_technicians (see the serializer's own
comment for why -- Conecto's post_save push has no guard on job.start,
so an assigned-but-undated job would reach a real phone with a blank
start instead of quietly waiting).
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.testing import ALL_SECTIONS, staff_user
from scheduling.models import Job

User = get_user_model()


class JobPoolTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.staff = staff_user("dispatcher", role=User.Role.SUPPORT)
        self.tech = staff_user("field-tech", role=User.Role.TECHNICIAN)
        self.client.force_authenticate(self.staff)

    # ---- creating a pool job -------------------------------------------

    def test_a_job_with_no_dates_lands_in_the_pool(self):
        res = self.client.post("/api/jobs/", {"title": "Fibre drop for Ms Nkosi"}, format="json")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertIsNone(res.data["start"])
        self.assertIsNone(res.data["end"])

    def test_a_pool_job_cannot_carry_a_technician(self):
        res = self.client.post(
            "/api/jobs/",
            {"title": "Fibre drop", "assigned_technicians": [self.tech.id]},
            format="json",
        )
        self.assertEqual(res.status_code, 400, res.data)
        self.assertIn("pool", str(res.data).lower())
        self.assertEqual(Job.objects.count(), 0)

    def test_start_without_end_is_refused(self):
        res = self.client.post(
            "/api/jobs/",
            {"title": "Half-set job", "start": timezone.now().isoformat()},
            format="json",
        )
        self.assertEqual(res.status_code, 400, res.data)

    def test_end_without_start_is_refused(self):
        res = self.client.post(
            "/api/jobs/",
            {"title": "Half-set job", "end": timezone.now().isoformat()},
            format="json",
        )
        self.assertEqual(res.status_code, 400, res.data)

    def test_an_ordinary_scheduled_job_is_unaffected(self):
        """The pool changes nothing about the existing create-with-a-date
        path -- this is the same request the calendar's New Job button
        always sent."""
        start = timezone.now()
        res = self.client.post(
            "/api/jobs/",
            {
                "title": "Router swap",
                "location": "12 Main Road",
                "start": start.isoformat(),
                "end": (start + timezone.timedelta(hours=1)).isoformat(),
                "assigned_technicians": [self.tech.id],
            },
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.data)
        self.assertIsNotNone(res.data["start"])
        self.assertEqual([self.tech.id], res.data["assigned_technicians"])

    # ---- pulling a job out of the pool (scheduling it) -----------------

    def test_scheduling_a_pool_job_sets_its_dates_and_technician(self):
        job = Job.objects.create(title="Fibre drop", location="12 Main Road")
        start = timezone.now()
        res = self.client.patch(
            f"/api/jobs/{job.id}/",
            {
                "start": start.isoformat(),
                "end": (start + timezone.timedelta(hours=1)).isoformat(),
                "assigned_technicians": [self.tech.id],
            },
            format="json",
        )
        self.assertEqual(res.status_code, 200, res.data)
        job.refresh_from_db()
        self.assertIsNotNone(job.start)
        self.assertEqual([self.tech.id], list(job.assigned_technicians.values_list("id", flat=True)))

    def test_scheduling_a_pool_job_without_a_technician_is_fine(self):
        """A pool job can be given a time with nobody assigned yet -- same
        as any ordinary unassigned scheduled job today."""
        job = Job.objects.create(title="Site survey", location="Plot 7, Bela-Bela")
        start = timezone.now()
        res = self.client.patch(
            f"/api/jobs/{job.id}/",
            {"start": start.isoformat(), "end": (start + timezone.timedelta(hours=1)).isoformat()},
            format="json",
        )
        self.assertEqual(res.status_code, 200, res.data)

    # ---- sending a job back to the pool --------------------------------

    def test_sending_a_scheduled_job_back_to_the_pool(self):
        start = timezone.now()
        job = Job.objects.create(
            title="Router swap", start=start, end=start + timezone.timedelta(hours=1),
        )
        job.assigned_technicians.set([self.tech])
        res = self.client.patch(
            f"/api/jobs/{job.id}/",
            {"start": None, "end": None, "assigned_technicians": []},
            format="json",
        )
        self.assertEqual(res.status_code, 200, res.data)
        job.refresh_from_db()
        self.assertIsNone(job.start)
        self.assertIsNone(job.end)
        self.assertEqual(0, job.assigned_technicians.count())

    def test_sending_back_to_the_pool_without_clearing_the_technician_is_refused(self):
        """Half of "send back to the pool" -- clearing the dates but
        leaving the technician assigned -- is exactly the state the pool
        can never hold, so it is refused rather than silently dropping
        the technician for you."""
        start = timezone.now()
        job = Job.objects.create(
            title="Router swap", start=start, end=start + timezone.timedelta(hours=1),
        )
        job.assigned_technicians.set([self.tech])
        res = self.client.patch(
            f"/api/jobs/{job.id}/", {"start": None, "end": None}, format="json"
        )
        self.assertEqual(res.status_code, 400, res.data)
        job.refresh_from_db()
        self.assertIsNotNone(job.start)
        self.assertEqual(1, job.assigned_technicians.count())

    def test_an_in_progress_job_cannot_be_sent_back_to_the_pool(self):
        start = timezone.now()
        job = Job.objects.create(
            title="Router swap", start=start, end=start + timezone.timedelta(hours=1),
            status=Job.Status.IN_PROGRESS,
        )
        res = self.client.patch(
            f"/api/jobs/{job.id}/", {"start": None, "end": None}, format="json"
        )
        self.assertEqual(res.status_code, 400, res.data)

    def test_a_completed_job_cannot_be_sent_back_to_the_pool(self):
        start = timezone.now()
        job = Job.objects.create(
            title="Router swap", start=start, end=start + timezone.timedelta(hours=1),
            status=Job.Status.COMPLETED,
        )
        res = self.client.patch(
            f"/api/jobs/{job.id}/", {"start": None, "end": None}, format="json"
        )
        self.assertEqual(res.status_code, 400, res.data)

    # ---- the pool list itself -------------------------------------------

    def test_unscheduled_filter_returns_only_pool_jobs(self):
        start = timezone.now()
        Job.objects.create(title="Scheduled job", start=start, end=start + timezone.timedelta(hours=1))
        pool_job = Job.objects.create(title="Pool job")
        # "true", not "1": the filter is a tri-state BooleanFilter
        # (True/False/unset) built on Django's NullBooleanField, which
        # parses "true"/"True" but leaves "1" as unset -- same convention
        # the existing overdue/high_alert boolean filters elsewhere in this
        # API already rely on (the frontend always sends "true").
        res = self.client.get("/api/jobs/?unscheduled=true&page_size=100")
        self.assertEqual(res.status_code, 200, res.data)
        ids = {row["id"] for row in res.data["results"]}
        self.assertEqual({pool_job.id}, ids)

    def test_unscheduled_1_is_not_recognised_as_true(self):
        """Pinning down the tri-state gotcha above explicitly: "1" is not
        one of NullBooleanField's true-ish tokens, so it must not silently
        filter to nothing (nor error) -- it leaves the filter unset and
        returns every job, scheduled or not."""
        start = timezone.now()
        scheduled = Job.objects.create(title="Scheduled job", start=start, end=start + timezone.timedelta(hours=1))
        pool_job = Job.objects.create(title="Pool job")
        res = self.client.get("/api/jobs/?unscheduled=1&page_size=100")
        self.assertEqual(res.status_code, 200, res.data)
        ids = {row["id"] for row in res.data["results"]}
        self.assertEqual({scheduled.id, pool_job.id}, ids)

    def test_a_wide_start_range_still_excludes_pool_jobs(self):
        """The calendar's own start_from/start_to filters must keep
        excluding pool jobs, however wide the range -- a NULL start never
        satisfies >= or <=, but this is worth pinning down explicitly
        rather than trusting that by inference."""
        start = timezone.now()
        scheduled = Job.objects.create(
            title="Scheduled job", start=start, end=start + timezone.timedelta(hours=1)
        )
        Job.objects.create(title="Pool job")
        far_past = (start - timezone.timedelta(days=3650)).isoformat()
        far_future = (start + timezone.timedelta(days=3650)).isoformat()
        # Built as a query dict, not an f-string URL: isoformat() on a
        # timezone-aware datetime ends in "+00:00", and a literal "+" in a
        # hand-built query string is read back as a space by the URL
        # decoder, breaking the timestamp. The test client encodes a dict
        # properly.
        res = self.client.get(
            "/api/jobs/", {"start_from": far_past, "start_to": far_future, "page_size": 100}
        )
        self.assertEqual(res.status_code, 200, res.data)
        ids = {row["id"] for row in res.data["results"]}
        self.assertEqual({scheduled.id}, ids)

    def test_pool_jobs_can_be_ordered_by_created_at_for_a_stable_fifo_list(self):
        first = Job.objects.create(title="First in")
        second = Job.objects.create(title="Second in")
        res = self.client.get("/api/jobs/?unscheduled=true&ordering=created_at&page_size=100")
        self.assertEqual(res.status_code, 200, res.data)
        ids = [row["id"] for row in res.data["results"]]
        self.assertEqual([first.id, second.id], ids)
