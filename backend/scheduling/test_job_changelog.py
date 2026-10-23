"""Scheduling "Job changes & changelog" (Rudolph, 8 Oct 2026: "add a Job
change & Changelog tile to see who schedule, changed the jobs etc")."""
import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.testing import staff_user
from customers.models import Customer
from scheduling.models import Job

User = get_user_model()


class JobChangelogTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.elone = staff_user("elone", role=User.Role.SUPPORT)
        self.elone.first_name, self.elone.last_name = "Elone", "Claassens"
        self.elone.save()
        self.tech = staff_user("christo", role=User.Role.TECHNICIAN)
        self.client.force_authenticate(self.elone)
        self.customer = Customer.objects.create(full_name="Elzane Meyer", email="em@example.com",
                                                address="32 Allan Street")
        self.start = timezone.now() + datetime.timedelta(days=1)

    def _log(self, **params):
        res = self.client.get("/api/jobs/changelog/", params)
        self.assertEqual(res.status_code, 200, res.data)
        return res.data

    def test_create_schedule_reassign_pool_delete_restore_are_logged_with_who(self):
        res = self.client.post("/api/jobs/", {"title": "Return visit", "customer": self.customer.id}, format="json")
        job_id = res.data["id"]
        self.client.patch(f"/api/jobs/{job_id}/", {
            "start": self.start.isoformat(), "end": (self.start + datetime.timedelta(hours=1)).isoformat(),
            "assigned_technicians": [self.tech.id],
        }, format="json")
        self.client.patch(f"/api/jobs/{job_id}/", {"start": None, "end": None, "assigned_technicians": []},
                          format="json")
        self.client.delete(f"/api/jobs/{job_id}/")
        self.client.post(f"/api/jobs/{job_id}/restore/")

        rows = self._log(job=job_id)["results"]
        self.assertTrue(all(r["who"] == "Elone Claassens (elone)" for r in rows), rows)
        self.assertEqual(rows[-1]["action"], "created")
        fields = [{c["field"] for c in r["changes"]} for r in rows]
        self.assertTrue(any("start" in f for f in fields))
        self.assertTrue(any("assigned_technicians" in f for f in fields))
        self.assertTrue(any("deleted_at" in f for f in fields))
        scheduled = next(r for r in rows if any(c["field"] == "start" and not c["from"] and c["to"]
                                                for c in r["changes"]))
        self.assertTrue(scheduled["job_exists"])

    def test_purged_job_stays_in_the_log(self):
        job = Job.objects.create(title="Mistake", customer=self.customer, location="x")
        self.client.delete(f"/api/jobs/{job.id}/")
        self.client.delete(f"/api/jobs/{job.id}/purge/")
        rows = self._log(job=job.id)["results"]
        self.assertEqual(rows[0]["action"], "deleted")
        self.assertFalse(rows[0]["job_exists"])
        self.assertIn("Mistake", rows[0]["label"])

    def test_search_and_paging(self):
        for n in range(30):
            Job.objects.create(title=f"Job {n}", location="x")
        Job.objects.create(title="Gaggle Inn toets", location="x")
        self.assertEqual(len(self._log(q="gaggle")["results"]), 1)
        first = self._log()
        self.assertEqual(len(first["results"]), 25)
        self.assertTrue(first["has_more"])
        self.assertFalse(self._log(page=2)["has_more"])

    def test_conecto_changes_are_labelled(self):
        from audit.context import acting_as_system

        job = Job.objects.create(title="Install", location="x")
        with acting_as_system("Conecto app"):
            job.status = Job.Status.COMPLETED
            job.save()
        rows = self._log(job=job.id)["results"]
        self.assertEqual(rows[0]["who"], "Conecto app")
