"""Conecto digital job cards (7 Oct 2026)."""
import io
from unittest.mock import patch

from django.test import TestCase, override_settings
from PIL import Image, ImageDraw
from rest_framework.test import APIClient

from accounts.models import User
from customers.models import Customer
from scheduling.models import Job
from tickets.models import Ticket

from .models import ConectoInternalTicket, ConectoJobCard

TOKEN = "test-shared-secret"
CONFIGURED = override_settings(CONECTO_API_KEY=TOKEN, CONECTO_WEBHOOK_TOKEN=TOKEN, CONECTO_URLS={})


def png():
    image = Image.new("RGBA", (200, 80), (0, 0, 0, 0))
    ImageDraw.Draw(image).line([(10, 60), (90, 10), (190, 70)], fill=(0, 0, 0, 255), width=4)
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


class FakeResponse:
    def __init__(self, body, status_code=200):
        self.body, self.status_code = body, status_code

    def iter_content(self, size):
        yield self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def post(path, body):
    return APIClient().post(path, body, format="json", HTTP_AUTHORIZATION=f"Bearer {TOKEN}")


@CONFIGURED
class JobCardTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(full_name="Diane Swart", customer_id="SWF2900")
        self.tech = User.objects.create_user(username="christo", password="x", role="technician",
                                             first_name="Christo")
        self.tech.refresh_from_db()
        self.job = Job.objects.create(title="Install SWF2900", customer=self.customer, job_type="installation")
        self.body = {
            "conecto_job_card_id": "jc-1", "conecto_job_id": "cj-9", "vexar_job_id": str(self.job.pk),
            "vexar_ticket_id": None, "client_code": "SWF2900", "staff_code": self.tech.staff_code,
            "technician_code": None, "technician_name": "Christo", "technician_names": ["Christo", "Piet"],
            "job_types": ["install", "panning"], "client_name": "Diane Swart", "address": "1 Lanzerac",
            "town": "Kroonstad", "package_mbps": 20, "package_price": 499, "insurance_wanted": "no",
            "equipment_ownership": "loan", "antenna_brand": "mikrotik", "router_brand": "tenda",
            "bracket_size": "medium", "pole_m": 3, "rj45": 4, "speed_down_mbps": 21.5, "speed_up_mbps": 9.8,
            "tower_name": "Vrischgewaagd", "signal": "-58", "line_of_sight": "los",
            "job_details": "Installed on the roof.\nAll good.", "client_signature_url": "https://example.com/sig.png",
            "client_signed_name": "Diane Swart", "client_unavailable_reason": None,
            "additional_work_needed": True, "additional_work_description": "Move router",
            "additional_work_ticket_id": "it-77", "checked_in_at": "2026-10-07T08:00:00+02:00",
            "completed_at": "2026-10-07T10:15:00+02:00", "duration_minutes": 135, "km_traveled": 22.4,
            "submitted_at": "2026-10-07T10:16:00+02:00",
        }
        patch("conecto.job_cards._public_host", return_value=True).start()
        self.addCleanup(patch.stopall)

    def send(self, body=None):
        with patch("requests.get", return_value=FakeResponse(png())):
            with self.captureOnCommitCallbacks(execute=True):
                return post("/api/webhooks/conecto/job-card/", body or self.body)

    def test_recorded_linked_and_signature_copied(self):
        res = self.send()
        self.assertEqual(res.status_code, 201, res.data)
        card = ConectoJobCard.objects.get(pk=int(res.data["job_card_id"]))
        self.assertEqual((card.job, card.customer, card.technician), (self.job, self.customer, self.tech))
        self.assertEqual(card.job_types, ["install", "panning"])
        self.assertTrue(bytes(card.signature_png).startswith(b"\x89PNG"))
        self.assertEqual(card.data["pole_m"], 3)

    def test_repeat_is_2xx_with_the_same_id(self):
        first = self.send()
        again = self.send()
        self.assertEqual((again.status_code, again.data["job_card_id"], again.data["duplicate"]),
                         (200, first.data["job_card_id"], True))
        self.assertEqual(ConectoJobCard.objects.count(), 1)

    def test_everything_null_but_the_id(self):
        res = self.send({"conecto_job_card_id": "jc-2", "client_code": None, "job_types": None,
                         "client_signature_url": None})
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(post("/api/webhooks/conecto/job-card/", {}).status_code, 400)
        self.assertEqual(APIClient().post("/api/webhooks/conecto/job-card/", {}, format="json").status_code, 401)

    def test_signature_failure_is_kept_for_retry(self):
        with patch("requests.get", return_value=FakeResponse(b"", 404)):
            with self.captureOnCommitCallbacks(execute=True):
                post("/api/webhooks/conecto/job-card/", self.body)
        card = ConectoJobCard.objects.get()
        self.assertIsNone(card.signature_png)
        self.assertIn("404", card.signature_error)
        from django.core.management import call_command

        with patch("requests.get", return_value=FakeResponse(png())):
            call_command("conecto_fetch_job_card_signatures", stdout=io.StringIO())
        card.refresh_from_db()
        self.assertTrue(card.signature_png)

    def test_http_only_links_refused(self):
        body = {**self.body, "conecto_job_card_id": "jc-3", "client_signature_url": "http://10.0.0.1/x.png"}
        self.send(body)
        self.assertIn("https", ConectoJobCard.objects.get(conecto_job_card_id="jc-3").signature_error)

    def test_completed_status_links_a_card_without_a_job(self):
        self.send({**self.body, "vexar_job_id": None, "conecto_job_id": None, "client_code": None})
        card = ConectoJobCard.objects.get()
        self.assertIsNone(card.job)
        res = post("/api/webhooks/conecto/job-status-changed/",
                   {"vexar_job_id": str(self.job.pk), "status": "completed", "job_card_id": "jc-1"})
        self.assertEqual(res.status_code, 200, res.data)
        card.refresh_from_db()
        self.assertEqual((card.job, card.customer), (self.job, self.customer))

    def test_additional_work_ticket_and_staff_views(self):
        self.send()
        res = post("/api/webhooks/conecto/internal-ticket-created/", {
            "conecto_ticket_id": "it-77", "category": "additional_work", "job_card_id": "jc-1",
            "vexar_job_id": str(self.job.pk), "description": "Move router",
        })
        self.assertEqual(res.status_code, 201, res.data)
        ticket = Ticket.objects.get(ticket_number=res.data["ticket_number"])
        self.assertEqual(ticket.subject, "Additional work needed")
        self.assertEqual(ConectoInternalTicket.objects.get().job_card_id, "jc-1")

        staff = User.objects.create_user(username="janine", password="x", role="support",
                                         allowed_sections=["customers"])
        api = APIClient()
        api.force_authenticate(staff)
        rows = api.get(f"/api/job-cards/?customer={self.customer.pk}").data
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["job_type_labels"], ["Installation", "Panning"])
        self.assertEqual(rows[0]["additional_work_ticket"]["number"], ticket.ticket_number)
        self.assertTrue(rows[0]["has_signature"])
        self.assertEqual(len(api.get(f"/api/job-cards/?job={self.job.pk}").data), 1)
        pdf = api.get(f"/api/job-cards/{rows[0]['id']}/pdf/")
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.content.startswith(b"%PDF"))
        tech_api = APIClient()
        nobody = User.objects.create_user(username="x", password="x", role="support", allowed_sections=["finance"])
        tech_api.force_authenticate(nobody)
        self.assertEqual(tech_api.get("/api/job-cards/").status_code, 403)
