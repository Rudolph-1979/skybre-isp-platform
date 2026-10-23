"""The sales flow Rudolph drew (6 Oct 2026): New -> Qualify -> Coverage
check (Electronic LOS / Scan LOS / Fibre check) -> Quote -> Accept / Reject,
Lost at any step with a reason."""
import datetime
from decimal import Decimal
from unittest import mock

from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from billing.models import Tariff
from customers.models import Partner
from scheduling.models import Job
from sales.models import Lead
from sales.workflow import next_action


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class SalesFlowTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="boss", password="x", role="admin", first_name="Rudolph")
        self.tech = User.objects.create_user(username="tech", password="x", role="technician", first_name="Fanie")
        self.api = APIClient()
        self.api.force_authenticate(self.admin)
        self.tariff = Tariff.objects.create(
            name="20 Mbps", price=Decimal("599.00"), tax_rate_pct=Decimal("15"),
            speed_download_kbps=20480, speed_upload_kbps=20480,
        )
        self.lead = Lead.objects.create(full_name="Jan Botha", email="jan@example.com", phone="082",
                                        address="Plot 12", city="Bela-Bela")

    def post(self, action, data=None, lead=None):
        return self.api.post(f"/api/leads/{(lead or self.lead).pk}/{action}/", data or {}, format="json")

    def test_electronic_los_pass_moves_to_coverage(self):
        res = self.post("coverage", {"method": "electronic", "result": "pass", "tower": "Bela-Bela North",
                                     "sector": "S2", "signal": "-58 dBm"})
        self.assertEqual(res.status_code, 200, res.data)
        lead = res.data["lead"]
        self.assertEqual(lead["status"], "coverage")
        self.assertEqual(lead["coverage_result_display"], "Pass")
        self.assertEqual(lead["coverage_checked_by_name"], "Rudolph")
        self.assertEqual(lead["next_action"]["text"], "Send quote")
        self.assertIn("Electronic LOS: Pass", self.lead.lead_notes.first().body)

    def test_wrong_result_for_method_is_refused(self):
        res = self.post("coverage", {"method": "electronic", "result": "fibre_confirmed"})
        self.assertEqual(res.status_code, 400)
        res = self.post("coverage", {"method": "fibre", "result": "fibre_confirmed"})
        self.assertEqual(res.status_code, 400)
        self.assertIn("fibre partner", res.data["detail"])

    def test_scan_los_books_a_site_visit(self):
        start = timezone.make_aware(datetime.datetime(2026, 10, 9, 10, 0))
        res = self.post("coverage", {"method": "scan", "scan_start": start.isoformat(), "scan_technician": self.tech.pk})
        self.assertEqual(res.status_code, 200, res.data)
        job = Job.objects.get(pk=res.data["lead"]["scan_job"])
        self.assertEqual(job.job_type, "site_visit")
        self.assertEqual(job.start, start)
        self.assertEqual(list(job.assigned_technicians.all()), [self.tech])
        self.assertIn("Plot 12", job.location)
        self.assertEqual(res.data["lead"]["scan"]["technicians"], ["Fanie"])
        # Rebooking moves the same job rather than making a second one.
        later = start + datetime.timedelta(days=1)
        self.post("coverage", {"method": "scan", "scan_start": later.isoformat(), "scan_technician": self.tech.pk})
        self.assertEqual(Job.objects.filter(job_type="site_visit").count(), 1)
        job.refresh_from_db()
        self.assertEqual(job.start, later)

    def test_scan_without_a_time_goes_to_the_pool(self):
        res = self.post("coverage", {"method": "scan"})
        job = Job.objects.get(pk=res.data["lead"]["scan_job"])
        self.assertIsNone(job.start)
        self.assertEqual(res.data["lead"]["next_action"]["text"], "Schedule the scan")

    def test_quote_needs_coverage_then_emails_a_pdf(self):
        res = self.post("quote", {"tariff": self.tariff.pk})
        self.assertEqual(res.status_code, 400)
        self.post("coverage", {"method": "electronic", "result": "pass"})
        locmem = mail.get_connection("django.core.mail.backends.locmem.EmailBackend")
        with mock.patch("sales.quote.render_quote_pdf", return_value=b"%PDF-fake"), \
                mock.patch("notifications.email_settings.get_email_connection", return_value=locmem):
            res = self.post("quote", {"tariff": self.tariff.pk, "valid_days": 7, "send_email": True,
                                      "items": [{"label": "Installation", "amount": "1500"}, {"label": "", "amount": "9"}]})
        self.assertEqual(res.status_code, 200, res.data)
        lead = res.data["lead"]
        self.assertEqual(lead["status"], "quoted")
        self.assertEqual(lead["quote_items"], [{"label": "Installation", "amount": "1500.00"}])
        self.assertEqual(lead["quote_totals"]["monthly"]["incl"], "688.85")
        self.assertEqual(lead["quote_totals"]["once_off"]["incl"], "1725.00")
        self.assertEqual(lead["quote_number"], f"Q-L{self.lead.pk:05d}")
        self.assertEqual(res.data["emailed_to"], "jan@example.com")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].attachments[0][0], f"Q-L{self.lead.pk:05d}.pdf")

    def test_quote_pdf_renders(self):
        self.post("coverage", {"method": "electronic", "result": "pass"})
        self.post("quote", {"tariff": self.tariff.pk, "items": [{"label": "Router", "amount": "850"}]})
        res = self.api.get(f"/api/leads/{self.lead.pk}/quote-pdf/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Type"], "application/pdf")
        self.assertTrue(res.content.startswith(b"%PDF"))

    def test_fibre_quote_must_be_a_partner_plan(self):
        vuma = Partner.objects.create(name="Vumatel")
        other = Partner.objects.create(name="Openserve")
        theirs = Tariff.objects.create(name="Vuma 50", price=Decimal("700"), partner=vuma,
                                       speed_download_kbps=1, speed_upload_kbps=1)
        wrong = Tariff.objects.create(name="OS 50", price=Decimal("700"), partner=other,
                                      speed_download_kbps=1, speed_upload_kbps=1)
        res = self.post("coverage", {"method": "fibre", "result": "fibre_confirmed", "coverage_partner": vuma.pk})
        self.assertEqual(res.data["lead"]["coverage_partner_name"], "Vumatel")
        self.assertEqual(self.post("quote", {"tariff": wrong.pk}).status_code, 400)
        self.assertEqual(self.post("quote", {"tariff": theirs.pk}).status_code, 200)
        res = self.post("accept")
        self.assertEqual(res.status_code, 200, res.data)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.customer.partner, vuma)

    def test_accept_creates_client_and_install_job_once(self):
        self.lead.latitude, self.lead.longitude = Decimal("-24.884100"), Decimal("28.290600")
        self.lead.save()
        self.post("coverage", {"method": "electronic", "result": "pass", "tower": "North"})
        self.post("quote", {"tariff": self.tariff.pk, "items": [{"label": "Installation", "amount": "1500"}]})
        res = self.post("accept")
        self.assertEqual(res.status_code, 200, res.data)
        lead = res.data["lead"]
        self.assertEqual(lead["status"], "won")
        self.assertEqual(lead["status_display"], "Accepted")
        job = Job.objects.get(pk=res.data["install_job_id"])
        self.assertEqual(job.job_type, "installation")
        self.assertIsNone(job.start)  # in the job pool
        self.assertEqual(job.customer_id, res.data["customer_id"])
        self.assertIn("Installation R 1500.00", job.description)
        self.assertIn("tower North", job.description)
        self.assertEqual(self.lead.__class__.objects.get(pk=self.lead.pk).customer.latitude, Decimal("-24.884100"))
        again = self.post("accept")
        self.assertEqual(again.data["install_job_id"], job.pk)
        self.assertEqual(Job.objects.filter(job_type="installation").count(), 1)

    def test_reject_records_reason_and_stage(self):
        self.post("coverage", {"method": "electronic", "result": "pass"})
        self.post("quote", {"tariff": self.tariff.pk})
        res = self.post("reject", {"reason": "price", "note": "Too dear vs competitor"})
        self.assertEqual(res.status_code, 200, res.data)
        lead = res.data["lead"]
        self.assertEqual((lead["status"], lead["lost_reason"], lead["lost_at_stage"]), ("lost", "price", "quoted"))
        self.assertEqual(lead["lost_at_stage_display"], "Quoted")
        self.assertIn("Too dear", self.lead.lead_notes.first().body)
        self.assertEqual(self.post("reject", {"reason": "price"}).status_code, 400)

    def test_lost_at_new_via_patch(self):
        res = self.api.patch(f"/api/leads/{self.lead.pk}/", {"status": "lost", "lost_reason": "duplicate"}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["lost_at_stage"], "new")
        res = self.api.patch(f"/api/leads/{self.lead.pk}/", {"status": "new", "lost_reason": ""}, format="json")
        self.assertEqual(res.data["lost_at_stage"], "")

    def test_no_los_reason_exists(self):
        res = self.post("reject", {"reason": "no_los"})
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["lead"]["lost_reason_display"], "No line of sight")

    def test_next_action_for_new_and_expired_quote(self):
        self.assertEqual(next_action(self.lead)["text"], "Qualify, or mark lost")
        self.lead.status = Lead.Status.QUOTED
        self.lead.quote_valid_until = timezone.localdate() - datetime.timedelta(days=1)
        self.assertTrue(next_action(self.lead)["late"])

    def test_pipeline_has_coverage_stage(self):
        stages = [s["status"] for s in self.api.get("/api/pipeline-summary/").data["stages"]]
        self.assertEqual(stages, ["new", "qualified", "coverage", "quoted", "won", "lost"])
