"""Urgent tag on jobs (Rudolph, 15 Oct 2026: "Can we create a Urgent Tag to Jobs?")."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.testing import staff_user
from conecto.client import job_notes
from scheduling.models import Job

User = get_user_model()


class JobUrgentTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(staff_user("dispatcher", role=User.Role.SUPPORT))

    def test_create_and_switch_off(self):
        res = self.client.post("/api/jobs/", {"title": "Link down at the clinic", "urgent": True}, format="json")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertTrue(res.data["urgent"])
        jid = res.data["id"]
        self.assertTrue(Job.objects.get(pk=jid).urgent)
        res = self.client.patch(f"/api/jobs/{jid}/", {"urgent": False}, format="json")
        self.assertFalse(res.data["urgent"])

    def test_defaults_to_not_urgent(self):
        res = self.client.post("/api/jobs/", {"title": "Site visit"}, format="json")
        self.assertFalse(res.data["urgent"])

    def test_technician_notes_start_with_urgent(self):
        job = Job.objects.create(title="Fix", description="Router dead", urgent=True)
        self.assertEqual(job_notes(job), "URGENT — Router dead")
        job.urgent = False
        self.assertEqual(job_notes(job), "Router dead")
        empty = Job.objects.create(title="Fix", urgent=True)
        self.assertEqual(job_notes(empty), "URGENT")
