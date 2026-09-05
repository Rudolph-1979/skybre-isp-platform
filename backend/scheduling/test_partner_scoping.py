"""A reseller-restricted staff member must not see other partners' jobs.

Every customer-linked list in the project is partner-scoped -- customers,
billing, sales, network, radiusauth, audit, bankfeeds. Scheduling was
not: JobViewSet.get_queryset was a flat Job.objects.all(), so a staff
member restricted to one reseller saw only that reseller's customers on
the Customers page and then every partner's installations on the
calendar, each card carrying the customer's name, site address and phone.
get_object() shares the queryset, so they could reschedule or delete a
competitor's job too.

customers/scoping.py's scope_by_customer was written with this model in
mind -- its docstring names "a scheduling Job with no customer is an
office task" as the reason allow_null defaults to True -- and scheduling
never called it.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from customers.models import Customer, Partner
from scheduling.models import Job, Shift

User = get_user_model()


class SchedulingPartnerScopingTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.acme = Partner.objects.create(name="Acme Fibre")
        self.rival = Partner.objects.create(name="Rival Networks")

        self.mine = Customer.objects.create(
            full_name="Acme Customer", email="ac@example.com", partner=self.acme
        )
        self.theirs = Customer.objects.create(
            full_name="Rival Customer", email="rc@example.com", partner=self.rival
        )
        self.direct = Customer.objects.create(
            full_name="Direct Customer", email="dc@example.com", partner=None
        )

        self.my_job = self._job(self.mine, "Acme install")
        self.their_job = self._job(self.theirs, "Rival install")
        self.direct_job = self._job(self.direct, "Direct install")
        self.office_job = self._job(None, "Stocktake")

        self.restricted = User.objects.create_user(
            username="acme-tech", password="pw-for-tests",
            role=User.Role.TECHNICIAN, allowed_partners=[self.acme.id],
        )
        self.unrestricted = User.objects.create_user(
            username="hq-tech", password="pw-for-tests", role=User.Role.TECHNICIAN
        )
        self.admin = User.objects.create_user(
            username="root", password="pw-for-tests",
            role=User.Role.ADMIN, allowed_partners=[self.acme.id],
        )

    def _job(self, customer, title):
        start = timezone.now()
        return Job.objects.create(
            customer=customer, title=title, start=start,
            end=start + timezone.timedelta(hours=1),
        )

    def _titles(self, user):
        self.client.force_authenticate(user)
        res = self.client.get("/api/jobs/?page_size=100")
        self.assertEqual(res.status_code, 200, res.data)
        return {row["title"] for row in res.data["results"]}

    # ---- the list ------------------------------------------------------

    def test_jobs_are_partner_scoped(self):
        titles = self._titles(self.restricted)
        self.assertIn("Acme install", titles)
        self.assertNotIn("Rival install", titles)

    def test_a_direct_customers_job_is_still_visible(self):
        """A customer with no partner isn't owned by any reseller, exactly
        as on the customer list."""
        self.assertIn("Direct install", self._titles(self.restricted))

    def test_a_standalone_office_job_is_still_visible(self):
        """Job.customer is blank for office work and internal errands.
        Those belong to no reseller and must not vanish from the calendar
        of a partner-restricted technician."""
        self.assertIn("Stocktake", self._titles(self.restricted))

    def test_unrestricted_staff_see_every_partner(self):
        titles = self._titles(self.unrestricted)
        self.assertIn("Acme install", titles)
        self.assertIn("Rival install", titles)

    def test_admin_sees_everything_despite_allowed_partners(self):
        titles = self._titles(self.admin)
        self.assertIn("Rival install", titles)

    # ---- individual jobs -----------------------------------------------

    def test_another_partners_job_cannot_be_read(self):
        self.client.force_authenticate(self.restricted)
        res = self.client.get(f"/api/jobs/{self.their_job.id}/")
        self.assertEqual(res.status_code, 404)

    def test_another_partners_job_cannot_be_rescheduled(self):
        self.client.force_authenticate(self.restricted)
        res = self.client.patch(f"/api/jobs/{self.their_job.id}/", {"title": "moved"}, format="json")
        self.assertEqual(res.status_code, 404)
        self.their_job.refresh_from_db()
        self.assertEqual(self.their_job.title, "Rival install")

    def test_another_partners_job_cannot_be_deleted(self):
        self.client.force_authenticate(self.restricted)
        res = self.client.delete(f"/api/jobs/{self.their_job.id}/")
        self.assertEqual(res.status_code, 404)
        self.assertTrue(Job.objects.filter(pk=self.their_job.pk).exists())

    def test_my_own_partners_job_is_still_editable(self):
        self.client.force_authenticate(self.restricted)
        res = self.client.patch(f"/api/jobs/{self.my_job.id}/", {"title": "moved"}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.my_job.refresh_from_db()
        self.assertEqual(self.my_job.title, "moved")


class ShiftsAreNotPartnerScopedTests(TestCase):
    """A Shift is a staff roster entry with no customer on it, so there is
    no partner dimension to filter on. Guarding that the Job fix did not
    quietly take the staff rota away from restricted staff."""

    def setUp(self):
        self.client = APIClient()
        acme = Partner.objects.create(name="Acme Fibre")
        self.restricted = User.objects.create_user(
            username="acme-tech", password="pw-for-tests",
            role=User.Role.TECHNICIAN, allowed_partners=[acme.id],
        )
        colleague = User.objects.create_user(
            username="someone-else", password="pw-for-tests", role=User.Role.TECHNICIAN
        )
        start = timezone.now()
        Shift.objects.create(
            staff=colleague, start=start, end=start + timezone.timedelta(hours=8),
            role_note="Field standby",
        )

    def test_a_restricted_technician_still_sees_the_rota(self):
        self.client.force_authenticate(self.restricted)
        res = self.client.get("/api/shifts/?page_size=100")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(1, len(res.data["results"]))
