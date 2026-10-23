"""Deleted jobs pool (Rudolph, 6 Oct 2026): delete moves a job to a
"Deleted jobs" pool; only deleting it from there removes it for good."""
import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from conecto.models import ConectoJob
from scheduling.models import Job

User = get_user_model()


class JobSoftDeleteTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="planner", password="x", role="support", allowed_sections=["scheduling"], first_name="Lani",
        )
        self.api = APIClient()
        self.api.force_authenticate(self.user)
        start = timezone.now().replace(microsecond=0) + datetime.timedelta(days=1)
        self.job = Job.objects.create(title="Install", start=start, end=start + datetime.timedelta(hours=1))
        self.pool_job = Job.objects.create(title="Waiting")

    def test_delete_moves_to_deleted_pool(self):
        res = self.api.delete(f"/api/jobs/{self.job.id}/")
        self.assertEqual(res.status_code, 204)
        self.job.refresh_from_db()
        self.assertIsNotNone(self.job.deleted_at)
        self.assertEqual(self.job.deleted_by, self.user)
        # Off the calendar / lists and not reachable by id...
        ids = [j["id"] for j in self.api.get("/api/jobs/?page_size=100").data["results"]]
        self.assertNotIn(self.job.id, ids)
        self.assertEqual(self.api.get(f"/api/jobs/{self.job.id}/").status_code, 404)
        # ...but in the Deleted pool, saying who and when.
        deleted = self.api.get("/api/jobs/deleted/").data
        self.assertEqual([d["id"] for d in deleted], [self.job.id])
        self.assertEqual(deleted[0]["deleted_by_name"], "Lani")

    def test_pool_job_deleted_leaves_the_job_pool(self):
        self.api.delete(f"/api/jobs/{self.pool_job.id}/")
        ids = [j["id"] for j in self.api.get("/api/jobs/?unscheduled=true").data["results"]]
        self.assertNotIn(self.pool_job.id, ids)

    def test_restore_puts_it_back(self):
        self.api.delete(f"/api/jobs/{self.job.id}/")
        res = self.api.post(f"/api/jobs/{self.job.id}/restore/")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertIsNone(res.data["deleted_at"])
        self.assertEqual(self.api.get(f"/api/jobs/{self.job.id}/").status_code, 200)
        self.assertEqual(self.api.get("/api/jobs/deleted/").data, [])

    def test_purge_only_from_the_deleted_pool(self):
        # A live job can't be purged directly...
        self.assertEqual(self.api.delete(f"/api/jobs/{self.job.id}/purge/").status_code, 404)
        self.api.delete(f"/api/jobs/{self.job.id}/")
        res = self.api.delete(f"/api/jobs/{self.job.id}/purge/")
        self.assertEqual(res.status_code, 204)
        self.assertFalse(Job.objects.filter(pk=self.job.pk).exists())

    def test_deleting_withdraws_from_phones_and_restore_resends(self):
        tech = User.objects.create_user(username="t", password="x", role="technician")
        link = ConectoJob.objects.create(job=self.job, technician=tech, technician_code="T1",
                                         conecto_job_id="abc", pushed_digest="d", last_status="pending")
        with patch("conecto.signals._cancel_job") as cancel, self.captureOnCommitCallbacks(execute=True):
            self.api.delete(f"/api/jobs/{self.job.id}/")
        # Not assigned any more in this test, so it's withdrawn either way;
        # the reason says why.
        self.assertTrue(cancel.called)
        with self.captureOnCommitCallbacks(execute=True):
            self.api.post(f"/api/jobs/{self.job.id}/restore/")
        link.refresh_from_db()
        self.assertEqual(link.pushed_digest, "")

    def test_deleted_job_is_withdrawn_with_deleted_reason(self):
        from conecto import signals

        tech = User.objects.create_user(username="t2", password="x", role="technician")
        self.job.assigned_technicians.set([tech])
        ConectoJob.objects.create(job=self.job, technician=tech, technician_code="T1",
                                  conecto_job_id="abc", pushed_digest="d", last_status="pending")
        Job.objects.filter(pk=self.job.pk).update(deleted_at=timezone.now())
        with patch("conecto.signals._cancel_job") as cancel:
            signals._schedule_job(self.job.pk)
        self.assertEqual(cancel.call_args[0][2], "Deleted in Skybre")
