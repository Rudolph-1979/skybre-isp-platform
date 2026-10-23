"""No scheduling without an address or GPS (Rudolph, 8 Oct 2026: "reject
scheduling if a address or coordinates are not inserted").

The job's Location, the client's street address / area, or the client's
GPS pin is enough. The pool, Completed and Cancelled jobs are exempt.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.testing import staff_user
from customers.models import Customer
from scheduling.models import Job

User = get_user_model()


class JobAddressRuleTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(staff_user("dispatcher", role=User.Role.SUPPORT))
        self.start = timezone.now() + timezone.timedelta(days=1)
        self.end = self.start + timezone.timedelta(hours=1)

    def _times(self, **extra):
        return {"start": self.start.isoformat(), "end": self.end.isoformat(), **extra}

    def _post(self, **extra):
        return self.client.post("/api/jobs/", {"title": "Install", **self._times(**extra)}, format="json")

    def test_no_location_and_no_client_is_refused(self):
        res = self._post()
        self.assertEqual(res.status_code, 400, res.data)
        self.assertIn("address or GPS", str(res.data))
        self.assertEqual(Job.objects.count(), 0)

    def test_client_without_address_or_gps_is_refused(self):
        customer = Customer.objects.create(full_name="No Address", email="na@example.com", city="Bela-Bela")
        res = self._post(customer=customer.id)
        self.assertEqual(res.status_code, 400, res.data)

    def test_typed_location_is_enough(self):
        self.assertEqual(self._post(location="-24.88, 28.29").status_code, 201)

    def test_client_street_address_is_enough(self):
        customer = Customer.objects.create(full_name="Street", email="st@example.com", address="4 Kerk Street")
        self.assertEqual(self._post(customer=customer.id).status_code, 201)

    def test_client_gps_pin_is_enough(self):
        customer = Customer.objects.create(
            full_name="Farm", email="fa@example.com", latitude=Decimal("-24.880000"), longitude=Decimal("28.290000"),
        )
        self.assertEqual(self._post(customer=customer.id).status_code, 201)

    def test_pool_job_needs_no_address(self):
        res = self.client.post("/api/jobs/", {"title": "Find the address"}, format="json")
        self.assertEqual(res.status_code, 201, res.data)

    def test_scheduling_a_pool_job_without_address_is_refused(self):
        job = Job.objects.create(title="Pool job")
        res = self.client.patch(f"/api/jobs/{job.id}/", self._times(), format="json")
        self.assertEqual(res.status_code, 400, res.data)
        job.refresh_from_db()
        self.assertIsNone(job.start)

    def test_clearing_the_location_of_a_scheduled_job_is_refused(self):
        job = Job.objects.create(title="Booked", location="4 Kerk Street", start=self.start, end=self.end)
        res = self.client.patch(f"/api/jobs/{job.id}/", {"location": ""}, format="json")
        self.assertEqual(res.status_code, 400, res.data)

    def test_completing_or_cancelling_an_old_job_without_address_is_allowed(self):
        job = Job.objects.create(title="Old", start=self.start, end=self.end)
        for status in ("completed", "cancelled"):
            res = self.client.patch(f"/api/jobs/{job.id}/", {"status": status}, format="json")
            self.assertEqual(res.status_code, 200, res.data)

    def test_title_edit_on_an_old_job_without_address_is_allowed(self):
        job = Job.objects.create(title="Old", start=self.start, end=self.end)
        res = self.client.patch(f"/api/jobs/{job.id}/", {"title": "Old (renamed)"}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
