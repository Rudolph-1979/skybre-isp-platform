"""Return to pool: the technician's Return button, and the daily sweep of
uncompleted jobs (Rudolph, 6 Oct 2026)."""
import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from conecto.models import ConectoJob
from scheduling.models import Job, SchedulingSettings
from scheduling.pool import return_to_pool, run_daily_return

User = get_user_model()
TOKEN = "test-shared-secret"


def local(y, m, d, h, mi=0):
    return timezone.make_aware(datetime.datetime(y, m, d, h, mi), timezone.get_current_timezone())


class ReturnToPoolTests(TestCase):
    def setUp(self):
        self.tech = User.objects.create_user(username="christo", password="x", role="technician", first_name="Christo")

    def _job(self, start, hours=1, status="scheduled"):
        job = Job.objects.create(title="Install", start=start, end=start + datetime.timedelta(hours=hours), status=status)
        job.assigned_technicians.set([self.tech])
        return job

    def test_return_clears_time_and_technicians_and_notes_why(self):
        job = self._job(local(2026, 10, 6, 9))
        self.assertTrue(return_to_pool(job, "Returned by Christo: client not home"))
        job.refresh_from_db()
        self.assertIsNone(job.start)
        self.assertEqual(list(job.assigned_technicians.all()), [])
        self.assertEqual(job.return_reason, "Returned by Christo: client not home")
        self.assertEqual(job.return_count, 1)
        self.assertEqual(job.status, "scheduled")

    def test_finished_jobs_never_come_back(self):
        done = self._job(local(2026, 10, 6, 9), status="completed")
        self.assertFalse(return_to_pool(done, "x"))

    def test_daily_sweep_at_the_set_time(self):
        SchedulingSettings.objects.update_or_create(pk=1, defaults={"auto_return_time": datetime.time(18, 0)})
        overdue = self._job(local(2026, 10, 6, 9))
        on_site = self._job(local(2026, 10, 6, 10), status="in_progress")
        later = self._job(local(2026, 10, 6, 19))
        done = self._job(local(2026, 10, 6, 8), status="completed")
        # Before the time: nothing.
        self.assertIsNone(run_daily_return(now=local(2026, 10, 6, 17, 55)))
        # At the time: the two whose time is over and aren't done.
        self.assertEqual(run_daily_return(now=local(2026, 10, 6, 18, 0)), 2)
        for j in (overdue, on_site, later, done):
            j.refresh_from_db()
        self.assertIsNone(overdue.start)
        self.assertIsNone(on_site.start)
        self.assertEqual(on_site.status, "scheduled")
        self.assertIn("returned automatically on 06 Oct 2026", overdue.return_reason)
        self.assertIsNotNone(later.start)
        self.assertEqual(done.status, "completed")
        # Once a day only.
        self.assertIsNone(run_daily_return(now=local(2026, 10, 6, 18, 5)))

    def test_switched_off_unless_forced(self):
        SchedulingSettings.objects.update_or_create(pk=1, defaults={"auto_return_enabled": False})
        self._job(local(2026, 10, 6, 9))
        self.assertIsNone(run_daily_return(now=local(2026, 10, 6, 23)))
        self.assertEqual(run_daily_return(now=local(2026, 10, 6, 23), force=True), 1)

    def test_settings_api(self):
        planner = User.objects.create_user(username="p", password="x", role="support", allowed_sections=["scheduling"])
        api = APIClient()
        api.force_authenticate(planner)
        res = api.patch("/api/scheduling-settings/", {"auto_return_time": "17:30"}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(SchedulingSettings.load().auto_return_time, datetime.time(17, 30))
        self.assertEqual(api.get("/api/scheduling-settings/").data["auto_return_enabled"], True)


@override_settings(CONECTO_API_KEY=TOKEN, CONECTO_WEBHOOK_TOKEN=TOKEN)
class TechnicianReturnTests(TestCase):
    def test_return_from_the_app(self):
        tech = User.objects.create_user(username="christo", password="x", role="technician", first_name="Christo")
        start = timezone.now() + datetime.timedelta(hours=1)
        job = Job.objects.create(title="Install", start=start, end=start + datetime.timedelta(hours=1))
        job.assigned_technicians.set([tech])
        ConectoJob.objects.create(job=job, technician=tech, technician_code="TECH-001", conecto_job_id="c1")
        with patch("conecto.signals._cancel_job"):
            res = APIClient().post(
                "/api/webhooks/conecto/job-status-changed/",
                {"vexar_job_id": str(job.pk), "status": "returned", "technician_code": "TECH-001",
                 "reason": "Client not home"},
                format="json", HTTP_AUTHORIZATION=f"Bearer {TOKEN}",
            )
        self.assertEqual(res.status_code, 200, res.data)
        self.assertTrue(res.data["returned_to_pool"])
        job.refresh_from_db()
        self.assertIsNone(job.start)
        self.assertEqual(job.return_reason, "Returned by Christo: Client not home")


@override_settings(CONECTO_API_KEY=TOKEN, CONECTO_WEBHOOK_TOKEN=TOKEN)
class ReturnReasonRequiredTests(TestCase):
    """A technician must say why the vehicle has to go back (14 Oct 2026)."""

    def setUp(self):
        self.tech = User.objects.create_user(username="christo2", password="x", role="technician", first_name="Christo")
        start = timezone.now() + datetime.timedelta(hours=1)
        self.job = Job.objects.create(title="Install", start=start, end=start + datetime.timedelta(hours=1))
        self.job.assigned_technicians.set([self.tech])
        ConectoJob.objects.create(job=self.job, technician=self.tech, technician_code="TECH-002", conecto_job_id="c2")

    def _post(self, **extra):
        with patch("conecto.signals._cancel_job"):
            return APIClient().post(
                "/api/webhooks/conecto/job-status-changed/",
                {"vexar_job_id": str(self.job.pk), "status": "returned_to_pool", "technician_code": "TECH-002", **extra},
                format="json", HTTP_AUTHORIZATION=f"Bearer {TOKEN}",
            )

    def test_no_reason_is_refused_and_the_job_stays_booked(self):
        for extra in ({}, {"return_reason": "   "}, {"return_reason": "no"}):
            res = self._post(**extra)
            self.assertEqual(res.status_code, 400, res.data)
            self.assertEqual(res.data["code"], "return_reason_required")
        self.job.refresh_from_db()
        self.assertIsNotNone(self.job.start)
        self.assertEqual(list(self.job.assigned_technicians.all()), [self.tech])
        self.assertEqual(ConectoJob.objects.get(job=self.job).last_status, "")

    def test_a_reason_returns_it(self):
        res = self._post(return_reason="Need a longer cable, back tomorrow")
        self.assertEqual(res.status_code, 200, res.data)
        self.job.refresh_from_db()
        self.assertIsNone(self.job.start)
        self.assertIn("Need a longer cable", self.job.return_reason)

    @override_settings(CONECTO_RETURN_REASON_REQUIRED=False, CONECTO_API_KEY=TOKEN, CONECTO_WEBHOOK_TOKEN=TOKEN)
    def test_switch_off_accepts_no_reason(self):
        self.assertEqual(self._post().status_code, 200)
