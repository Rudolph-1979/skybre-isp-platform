"""Conecto, 7 Oct 2026 (second batch): who the technician is visiting on
scheduleJob, statementCreated on a schedule, and document links on the
portal host. Nothing here touches the network."""
import datetime
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from billing.models import CustomerBillingConfig, Invoice
from customers.models import Customer
from sales.models import Lead
from scheduling.models import Job

from .client import job_contact
from .models import ConectoTechnician

User = get_user_model()
TOKEN = "test-shared-secret"
URLS = {
    "scheduleJob": "https://example.invalid/scheduleJob",
    "cancelJob": "https://example.invalid/cancelJob",
    "statementCreated": "https://example.invalid/statementCreated",
}
CONFIGURED = override_settings(CONECTO_API_KEY=TOKEN, CONECTO_WEBHOOK_TOKEN=TOKEN, CONECTO_URLS=URLS)


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = ""
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._payload


@CONFIGURED
class ScheduleJobContactTests(TestCase):
    def setUp(self):
        self.tech = User.objects.create_user(username="christo", password="x", role="technician")
        ConectoTechnician.objects.create(user=self.tech, technician_code="TECH-001")
        self.start = timezone.now().replace(microsecond=0) + datetime.timedelta(days=1)

    def _book(self, request, **job):
        request.return_value = FakeResponse(payload={"job_id": "cn-1"})
        with self.captureOnCommitCallbacks(execute=True):
            j = Job.objects.create(title="Install", start=self.start,
                                   end=self.start + datetime.timedelta(hours=1), **job)
            j.assigned_technicians.set([self.tech])
        return request.call_args.kwargs["json"]

    def test_name_and_numbers_are_sent(self):
        customer = Customer.objects.create(
            full_name="Jan Botha", company_name="Botha Boerdery", customer_id="CUS-000700",
            phone="082 555 0001", contact_person_phone="083 555 0002",
        )
        with patch("conecto.client.requests.request") as request:
            body = self._book(request, customer=customer)
        self.assertEqual(body["client_name"], "Botha Boerdery (Jan Botha)")
        self.assertEqual(body["client_phone"], "082 555 0001")
        self.assertEqual(body["client_phone_alt"], "083 555 0002")

    def test_blank_numbers_are_left_out(self):
        customer = Customer.objects.create(full_name="Ria", customer_id="CUS-000701")
        with patch("conecto.client.requests.request") as request:
            body = self._book(request, customer=customer)
        self.assertEqual(body["client_name"], "Ria")
        self.assertNotIn("client_phone", body)
        self.assertNotIn("client_phone_alt", body)

    def test_a_lead_scan_uses_the_lead(self):
        job = Job.objects.create(title="LOS scan")
        Lead.objects.create(full_name="Petrus Venter", phone="071 000 1111", scan_job=job)
        self.assertEqual(job_contact(job)["client_name"], "Petrus Venter")
        self.assertEqual(job_contact(job)["client_phone"], "071 000 1111")


@CONFIGURED
class StatementScheduleTests(TestCase):
    def setUp(self):
        today = timezone.localdate()
        self.period_start = (today.replace(day=1) - datetime.timedelta(days=1)).replace(day=1)
        self.period = self.period_start.strftime("%Y-%m")

        def client(code, balance="0", invoiced=False, billing=True):
            c = Customer.objects.create(full_name=code, customer_id=code, balance=Decimal(balance))
            cfg = CustomerBillingConfig.for_customer(c)
            cfg.billing_enabled = billing
            cfg.save()
            if invoiced:
                inv = Invoice.objects.create(customer=c, status="paid", date_due=self.period_start)
                Invoice.objects.filter(pk=inv.pk).update(date_issued=self.period_start + datetime.timedelta(days=2))
            return c

        client("CUS-000801", invoiced=True)
        client("CUS-000802", balance="120.00")
        client("CUS-000803")                     # nothing to read
        client("CUS-000804", balance="50", billing=False)

    def _run(self, *args):
        out = StringIO()
        call_command("conecto_push_statements", *args, stdout=out)
        return out.getvalue()

    def test_dry_run_lists_and_sends_nothing(self):
        with patch("conecto.client.requests.request") as request:
            out = self._run()
        request.assert_not_called()
        self.assertIn("CUS-000801", out)
        self.assertIn("CUS-000802", out)
        self.assertNotIn("CUS-000803", out)
        self.assertNotIn("CUS-000804", out)

    def test_apply_sends_once_per_month(self):
        with patch("conecto.client.requests.request") as request:
            request.return_value = FakeResponse(payload={"success": True})
            self._run("--apply")
            self.assertEqual(request.call_count, 2)
            self.assertEqual(request.call_args.kwargs["json"]["period"], self.period)
            out = self._run("--apply")
            self.assertEqual(request.call_count, 2)
        self.assertIn("already sent 2", out)


class DocumentLinkHostTests(SimpleTestCase):
    def test_links_default_to_the_portal(self):
        self.assertEqual(settings.CONECTO_PUBLIC_BASE_URL, "https://portal.skybre.co.za")
