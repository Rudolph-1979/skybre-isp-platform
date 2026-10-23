"""A client's quote on a job, and booking a job from a ticket (Rudolph,
8 Oct 2026: "Allow me to attach a customers quote to a job that is being
scheduled and I need to be able to create a in pool schedule job from a
ticket" / "This ticket requires to be rescheduled and I need a option to
schedule here before I resolve").
"""
import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.testing import staff_user
from billing.models import Invoice, InvoiceItem
from conecto.client import job_notes
from conecto.models import ConectoInternalTicket
from customers.models import Customer
from scheduling.models import Job
from tickets.models import Ticket

User = get_user_model()


class JobQuoteTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(staff_user("dispatcher", role=User.Role.SUPPORT))
        self.elzane = Customer.objects.create(full_name="Elzane Meyer", email="em@example.com",
                                              address="32 Allan Street", city="Kroonstad")
        self.other = Customer.objects.create(full_name="Other", email="o@example.com", address="1 Main Rd")
        self.quote = self._doc(self.elzane, Invoice.Status.QUOTE)

    def _doc(self, customer, status):
        doc = Invoice.objects.create(customer=customer, status=status,
                                     date_due=timezone.localdate() + datetime.timedelta(days=14))
        InvoiceItem.objects.create(invoice=doc, description="Pole 3m", quantity=Decimal("2"),
                                   unit_price=Decimal("250"), tax_rate_pct=Decimal("15"))
        InvoiceItem.objects.create(invoice=doc, description="CAT6 outdoor cable (m)", quantity=Decimal("30"),
                                   unit_price=Decimal("8"), tax_rate_pct=Decimal("15"))
        doc.recalc_totals()
        doc.save()
        return doc

    def _post(self, **extra):
        return self.client.post("/api/jobs/", {"title": "Install", "location": "32 Allan Street", **extra},
                                format="json")

    def test_attach_the_clients_quote(self):
        res = self._post(customer=self.elzane.id, quote=self.quote.id)
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["quote_number"], self.quote.number)
        self.assertEqual(Job.objects.get().quote_id, self.quote.id)

    def test_pro_forma_can_be_attached(self):
        proforma = self._doc(self.elzane, Invoice.Status.PROFORMA)
        self.assertEqual(self._post(customer=self.elzane.id, quote=proforma.id).status_code, 201)

    def test_another_clients_quote_is_refused(self):
        theirs = self._doc(self.other, Invoice.Status.QUOTE)
        res = self._post(customer=self.elzane.id, quote=theirs.id)
        self.assertEqual(res.status_code, 400, res.data)
        self.assertIn("another client", str(res.data))

    def test_quote_without_a_client_is_refused(self):
        self.assertEqual(self._post(quote=self.quote.id).status_code, 400)

    def test_an_invoice_is_not_a_quote(self):
        self.quote.convert_to_invoice()
        self.assertEqual(self._post(customer=self.elzane.id, quote=self.quote.id).status_code, 400)

    def test_kept_after_the_quote_becomes_an_invoice(self):
        job = Job.objects.create(title="Install", customer=self.elzane, quote=self.quote, location="x")
        self.quote.convert_to_invoice()
        res = self.client.patch(f"/api/jobs/{job.id}/", {"title": "Install (renamed)", "quote": self.quote.id},
                                format="json")
        self.assertEqual(res.status_code, 200, res.data)

    def test_quote_lines_go_to_the_technician(self):
        job = Job.objects.create(title="Install", customer=self.elzane, quote=self.quote,
                                 description="Bring the long ladder")
        notes = job_notes(job)
        self.assertTrue(notes.startswith("Bring the long ladder"))
        self.assertIn(f"Quote {self.quote.number}", notes)
        self.assertIn("- 2 x Pole 3m", notes)
        self.assertIn("- 30 x CAT6 outdoor cable (m)", notes)
        self.assertEqual(job_notes(Job(title="x", description="Only notes")), "Only notes")


class JobFromTicketTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(staff_user("dispatcher", role=User.Role.SUPPORT))
        self.tech = staff_user("tech", role=User.Role.TECHNICIAN)
        self.elzane = Customer.objects.create(full_name="Elzane Meyer", email="em@example.com",
                                              address="32 Allan Street", city="Kroonstad")
        start = timezone.now() - datetime.timedelta(hours=3)
        self.original = Job.objects.create(title="Install", customer=self.elzane, job_type="installation",
                                           start=start, end=start + datetime.timedelta(hours=1))
        self.original.assigned_technicians.set([self.tech])
        self.ticket = Ticket.objects.create(is_internal=True, customer=None,
                                            subject="Return visit needs booking — 32 Allan Street Kroonstad",
                                            description="Moet paving lig en nuwe kabel trek")
        ConectoInternalTicket.objects.create(ticket=self.ticket, conecto_ticket_id="int-24", job=self.original)

    def test_defaults_come_from_the_job_the_ticket_was_raised_on(self):
        res = self.client.get(f"/api/jobs/ticket-defaults/?ticket={self.ticket.id}")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["customer"], self.elzane.id)
        self.assertEqual(res.data["job_type"], "installation")
        self.assertEqual(res.data["assigned_technicians"], [self.tech.id])
        self.assertIn("32 Allan Street", res.data["location"])
        self.assertTrue(res.data["title"].startswith(self.ticket.ticket_number))
        self.assertEqual(res.data["description"], "Moet paving lig en nuwe kabel trek")

    def test_customer_ticket_uses_its_own_client(self):
        ticket = Ticket.objects.create(customer=self.elzane, subject="No signal")
        res = self.client.get(f"/api/jobs/ticket-defaults/?ticket={ticket.id}")
        self.assertEqual(res.data["customer"], self.elzane.id)
        self.assertIsNone(res.data["source_job"])
        self.assertIn("Kroonstad", res.data["location"])

    def test_unknown_ticket(self):
        self.assertEqual(self.client.get("/api/jobs/ticket-defaults/?ticket=999999").status_code, 404)
        self.assertEqual(self.client.get("/api/jobs/ticket-defaults/").status_code, 404)

    def test_pool_job_from_the_ticket_and_listed_by_ticket(self):
        res = self.client.post("/api/jobs/", {
            "title": "Return visit", "ticket": self.ticket.id, "customer": self.elzane.id, "job_type": "site_visit",
        }, format="json")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertIsNone(res.data["start"])
        listed = self.client.get(f"/api/jobs/?ticket={self.ticket.id}")
        self.assertEqual([j["id"] for j in listed.data["results"]], [res.data["id"]])
