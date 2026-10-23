"""Scheduling -> Job types (Rudolph, 5 Oct 2026)."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from scheduling.models import Job, JobType

User = get_user_model()


class JobTypeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="sched", password="pw-for-tests-123", role="support", allowed_sections=["scheduling"],
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_built_in_types_are_seeded_with_their_colours(self):
        res = self.client.get("/api/job-types/")
        self.assertEqual(res.status_code, 200)
        keys = [r["key"] for r in res.data]
        self.assertEqual(keys, ["installation", "repair", "maintenance", "site_visit", "office_task", "other"])
        self.assertEqual(res.data[0]["colour"], "#2a78d6")

    def test_create_makes_a_key_and_a_job_can_use_it(self):
        res = self.client.post("/api/job-types/", {"name": "Fibre  Splice", "colour": "#AA00CC"}, format="json")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["key"], "fibre_splice")
        self.assertEqual(res.data["colour"], "#aa00cc")
        self.assertEqual(res.data["name"], "Fibre Splice")
        job = self.client.post("/api/jobs/", {"title": "Splice", "job_type": "fibre_splice"}, format="json")
        self.assertEqual(job.status_code, 201, job.data)
        listed = {r["key"]: r["job_count"] for r in self.client.get("/api/job-types/").data}
        self.assertEqual(listed["fibre_splice"], 1)

    def test_duplicate_name_and_bad_colour_refused(self):
        self.assertEqual(
            self.client.post("/api/job-types/", {"name": "repair", "colour": "#000000"}, format="json").status_code, 400
        )
        self.assertEqual(
            self.client.post("/api/job-types/", {"name": "Tower climb", "colour": "red"}, format="json").status_code, 400
        )

    def test_unknown_type_refused_on_job(self):
        res = self.client.post("/api/jobs/", {"title": "X", "job_type": "nope"}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertIn("job_type", res.data)

    def test_switched_off_type(self):
        Job.objects.create(title="Old one", job_type="repair")
        repair = JobType.objects.get(key="repair")
        self.client.patch(f"/api/job-types/{repair.id}/", {"is_active": False}, format="json")
        # Not for a new job...
        res = self.client.post("/api/jobs/", {"title": "New", "job_type": "repair"}, format="json")
        self.assertEqual(res.status_code, 400)
        # ...but the old job can still be edited.
        old = Job.objects.get(title="Old one")
        res = self.client.patch(f"/api/jobs/{old.id}/", {"title": "Old one, edited", "job_type": "repair"}, format="json")
        self.assertEqual(res.status_code, 200, res.data)

    def test_rename_keeps_key(self):
        repair = JobType.objects.get(key="repair")
        res = self.client.patch(f"/api/job-types/{repair.id}/", {"name": "Fault repair"}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["key"], "repair")

    def test_delete_only_when_unused(self):
        Job.objects.create(title="Uses it", job_type="maintenance")
        maint = JobType.objects.get(key="maintenance")
        res = self.client.delete(f"/api/job-types/{maint.id}/")
        self.assertEqual(res.status_code, 400)
        self.assertIn("Switch it off", res.data["detail"])
        other = JobType.objects.get(key="other")
        self.assertEqual(self.client.delete(f"/api/job-types/{other.id}/").status_code, 204)

    def test_needs_scheduling_access(self):
        outsider = User.objects.create_user(
            username="fin", password="pw-for-tests-123", role="accounts", allowed_sections=["finance"],
        )
        self.client.force_authenticate(outsider)
        self.assertEqual(self.client.get("/api/job-types/").status_code, 403)
