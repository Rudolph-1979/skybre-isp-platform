"""Jobs link themselves to the client whose code is in the title (7 Oct 2026)."""
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from customers.models import Customer
from scheduling.models import Job


class ClientLinkTests(TestCase):
    def setUp(self):
        self.thami = Customer.objects.create(full_name="Thami Guzana", customer_id="SR2547", phone="071 000 2547")

    def test_new_job_links_by_code(self):
        job = Job.objects.create(title="Thami Guzana sr2547 KPS Rental install")
        self.assertEqual(job.customer, self.thami)

    def test_unknown_code_or_no_code_stays_unlinked(self):
        self.assertIsNone(Job.objects.create(title="Olumide KRD Scan & kwotasie").customer)
        self.assertIsNone(Job.objects.create(title="Someone SWF9999 install").customer)

    def test_a_picked_client_is_never_replaced(self):
        other = Customer.objects.create(full_name="Other", customer_id="SWF1000")
        job = Job.objects.create(title="SR2547 relocation", customer=other)
        self.assertEqual(job.customer, other)

    def test_command_links_existing_jobs(self):
        job = Job.objects.create(title="placeholder")
        Job.objects.filter(pk=job.pk).update(title="Thami SR2547 install")
        out = StringIO()
        call_command("link_jobs_to_clients", stdout=out)
        job.refresh_from_db()
        self.assertIsNone(job.customer)
        self.assertIn("Would link 1", out.getvalue())
        call_command("link_jobs_to_clients", "--apply", stdout=StringIO())
        job.refresh_from_db()
        self.assertEqual(job.customer, self.thami)
