"""Conecto, 7 Oct 2026 (fourth batch): job-pool notifications (poolJobEvent),
the sales screens in the app (leads / lead / action) with their notifications
(salesEvent), and the paced invoice backfill. Nothing here touches the
network."""
import datetime
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from billing.models import Tariff
from customers.models import Customer
from sales.models import Lead, LeadNote
from scheduling.models import Job
from scheduling.pool import return_to_pool

from .models import ConectoTechnician

User = get_user_model()
TOKEN = "test-shared-secret"
URLS = {
    "poolJobEvent": "https://example.invalid/poolJobEvent",
    "salesEvent": "https://example.invalid/salesEvent",
    "scheduleJob": "https://example.invalid/scheduleJob",
    "cancelJob": "https://example.invalid/cancelJob",
}
CONFIGURED = override_settings(
    CONECTO_API_KEY=TOKEN, CONECTO_WEBHOOK_TOKEN=TOKEN, CONECTO_URLS=URLS,
    CONECTO_PUBLIC_BASE_URL="https://portal.skybre.co.za",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"success": True}
        self.text = ""
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._payload


def bodies(request, endpoint):
    return [c.kwargs["json"] for c in request.call_args_list if c.args[1].endswith(endpoint)]


def post(path, body):
    return APIClient().post(path, body, format="json", HTTP_AUTHORIZATION=f"Bearer {TOKEN}")


@CONFIGURED
@patch("conecto.client.requests.request")
class PoolJobEventTests(TestCase):
    def setUp(self):
        self.tech = User.objects.create_user(username="christo", password="x", role="technician", first_name="Christo")
        ConectoTechnician.objects.create(user=self.tech, technician_code="TECH-001")
        self.customer = Customer.objects.create(full_name="Lucille Smit", customer_id="CUS-001600", city="Kroonstad")

    def test_new_job_without_a_time(self, request):
        request.return_value = FakeResponse()
        with self.captureOnCommitCallbacks(execute=True):
            job = Job.objects.create(customer=self.customer, title="Router swap", job_type="maintenance")
        sent = bodies(request, "poolJobEvent")
        self.assertEqual(len(sent), 1)
        body = sent[0]
        self.assertEqual((body["vexar_job_id"], body["reason"], body["client_code"], body["client_name"], body["city"]),
                         (str(job.pk), "new", "CUS-001600", "Lucille Smit", "Kroonstad"))
        self.assertEqual(body["title"], "Router swap")
        self.assertTrue(body["job_type"])
        self.assertTrue(body["created_at"])

    def test_a_booked_job_is_not_news_and_edits_in_the_pool_are_quiet(self, request):
        request.return_value = FakeResponse(payload={"job_id": "cn-1"})
        start = timezone.now() + datetime.timedelta(hours=3)
        with self.captureOnCommitCallbacks(execute=True):
            Job.objects.create(customer=self.customer, title="Install", start=start,
                               end=start + datetime.timedelta(hours=1))
        self.assertEqual(bodies(request, "poolJobEvent"), [])
        with self.captureOnCommitCallbacks(execute=True):
            pooled = Job.objects.create(title="No client")
        request.reset_mock()
        with self.captureOnCommitCallbacks(execute=True):
            pooled.description = "edited"
            pooled.save()
        self.assertEqual(bodies(request, "poolJobEvent"), [])

    def test_no_client_sends_nulls(self, request):
        request.return_value = FakeResponse()
        with self.captureOnCommitCallbacks(execute=True):
            Job.objects.create(title="Tower check")
        body = bodies(request, "poolJobEvent")[0]
        self.assertEqual((body["client_code"], body["client_name"], body["city"]), (None, None, None))

    def _booked(self, request):
        request.return_value = FakeResponse(payload={"job_id": "cn-1"})
        start = timezone.now().replace(microsecond=0) + datetime.timedelta(hours=2)
        with self.captureOnCommitCallbacks(execute=True):
            job = Job.objects.create(customer=self.customer, title="Install", start=start,
                                     end=start + datetime.timedelta(hours=1))
            job.assigned_technicians.set([self.tech])
        request.reset_mock()
        request.return_value = FakeResponse()
        return job

    def test_unscheduled_by_the_sweep(self, request):
        job = self._booked(request)
        with self.captureOnCommitCallbacks(execute=True):
            return_to_pool(job, "Not completed — returned automatically")
        self.assertEqual([b["reason"] for b in bodies(request, "poolJobEvent")], ["unscheduled"])

    def test_unscheduled_by_the_office(self, request):
        job = self._booked(request)
        with self.captureOnCommitCallbacks(execute=True):
            job.start = job.end = None
            job.save()
        self.assertEqual([b["reason"] for b in bodies(request, "poolJobEvent")], ["unscheduled"])
        self.assertIn("cancelJob", [c.args[1].rsplit("/", 1)[-1] for c in request.call_args_list],
                      "the technician's copy comes off their phone")

    def test_returned_by_the_technician(self, request):
        job = self._booked(request)
        with self.captureOnCommitCallbacks(execute=True):
            res = post("/api/webhooks/conecto/job-status-changed/",
                       {"job_id": "cn-1", "vexar_job_id": str(job.pk), "status": "returned_to_pool",
                        "technician_code": "TECH-001", "return_reason": "client not home"})
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual([b["reason"] for b in bodies(request, "poolJobEvent")], ["returned"])


class SalesBase(TestCase):
    def setUp(self):
        self.lani = User.objects.create_user(username="lani", password="x", role="sales", first_name="Lani",
                                             allowed_sections=["sales"])
        self.piet = User.objects.create_user(username="piet", password="x", role="sales", first_name="Piet",
                                             allowed_sections=["sales"])
        self.boss = User.objects.create_user(username="boss", password="x", role="admin", first_name="Rudolph")
        self.tech = User.objects.create_user(username="tech", password="x", role="technician", first_name="Fanie",
                                             allowed_sections=["scheduling"])
        for u in (self.lani, self.piet, self.boss, self.tech):
            u.refresh_from_db()
        self.tariff = Tariff.objects.create(name="20 Mbps", price=Decimal("599.00"), tax_rate_pct=Decimal("15"))
        today = timezone.localdate()
        self.a = Lead.objects.create(full_name="Annes Venter", city="Parys", assigned_to=self.lani, status="qualified",
                                     interested_tariff=self.tariff, next_follow_up=today - datetime.timedelta(days=1))
        self.b = Lead.objects.create(full_name="Bennie Joubert", city="Sasolburg", assigned_to=self.lani, status="new")
        self.c = Lead.objects.create(full_name="Chris Nel", assigned_to=self.lani, status="lost", lost_reason="no_los")
        self.d = Lead.objects.create(full_name="Dirk Piet's lead", assigned_to=self.piet, status="new")


@CONFIGURED
class SalesLeadsTests(SalesBase):
    def ask(self, user, **body):
        return post("/api/conecto/sales/leads/", {"staff_code": user.staff_code, **body})

    def test_mine_open_by_default(self):
        res = self.ask(self.lani)
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual({r["id"] for r in res.data["results"]}, {self.a.pk, self.b.pk})
        row = next(r for r in res.data["results"] if r["id"] == self.a.pk)
        self.assertEqual((row["owner"], row["owner_staff_code"], row["tariff_name"], row["follow_up_is_due"]),
                         ("Lani", self.lani.staff_code, "20 Mbps", True))
        self.assertIn("text", row["next_action"])

    def test_views_stage_search(self):
        self.assertEqual([r["id"] for r in self.ask(self.lani, view="due").data["results"]], [self.a.pk])
        self.assertEqual(self.ask(self.lani, view="all").data["count"], 3)
        self.assertEqual([r["id"] for r in self.ask(self.lani, view="all", stage="lost").data["results"]], [self.c.pk])
        self.assertEqual([r["id"] for r in self.ask(self.lani, search="sasol").data["results"]], [self.b.pk])

    def test_team_is_for_admin_and_management(self):
        self.assertEqual(self.ask(self.lani, scope="team").status_code, 403)
        res = self.ask(self.boss, scope="team", view="all")
        self.assertEqual(res.data["count"], 4)

    def test_paging(self):
        for i in range(30):
            Lead.objects.create(full_name=f"Bulk {i}", assigned_to=self.piet)
        first = self.ask(self.piet, view="all")
        self.assertEqual((first.data["count"], len(first.data["results"]), first.data["has_more"]), (31, 25, True))
        second = self.ask(self.piet, view="all", page=2)
        self.assertEqual((len(second.data["results"]), second.data["has_more"]), (6, False))

    def test_refusals(self):
        self.assertEqual(post("/api/conecto/sales/leads/", {"staff_code": "STF-999999"}).status_code, 404)
        self.assertEqual(self.ask(self.tech).status_code, 403)
        self.assertEqual(self.ask(self.lani, view="later").status_code, 400)
        self.assertEqual(self.ask(self.lani, stage="nope").status_code, 400)
        self.assertEqual(APIClient().post("/api/conecto/sales/leads/", {}, format="json").status_code, 401)


@CONFIGURED
class SalesLeadTests(SalesBase):
    def test_detail_with_timeline_newest_first(self):
        LeadNote.objects.create(lead=self.a, author=self.lani, kind="call", body="first")
        LeadNote.objects.create(lead=self.a, author=self.lani, kind="note", body="second")
        res = post("/api/conecto/sales/lead/", {"staff_code": self.lani.staff_code, "lead_id": self.a.pk})
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["full_name"], "Annes Venter")
        self.assertEqual([n["body"] for n in res.data["timeline"]][:2], ["second", "first"])
        self.assertIsNone(res.data["quote_pdf_url"])

    def test_someone_elses_lead(self):
        res = post("/api/conecto/sales/lead/", {"staff_code": self.lani.staff_code, "lead_id": self.d.pk})
        self.assertEqual(res.status_code, 404)
        res = post("/api/conecto/sales/lead/", {"staff_code": self.boss.staff_code, "lead_id": self.d.pk})
        self.assertEqual(res.status_code, 200)

    def test_quote_pdf_link_works_and_is_signed(self):
        Lead.objects.filter(pk=self.a.pk).update(
            quote_sent_at=timezone.now(), coverage_method="electronic", coverage_result="pass",
            quote_items=[{"description": "Installation", "amount": "1500.00"}],
            quote_valid_until=timezone.localdate() + datetime.timedelta(days=14),
        )
        res = post("/api/conecto/sales/lead/", {"staff_code": self.lani.staff_code, "lead_id": self.a.pk})
        url = res.data["quote_pdf_url"]
        self.assertTrue(url.startswith("https://portal.skybre.co.za/api/public/lead-quote/"), url)
        self.assertTrue(res.data["quote_pdf_expires_at"])
        path = url.replace("https://portal.skybre.co.za", "")
        with patch("sales.quote.render_quote_pdf", return_value=b"%PDF-1.4 test"):
            pdf = APIClient().get(path)
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertEqual(APIClient().get(path[:-3] + "xx/").status_code, 404)


@CONFIGURED
class SalesActionTests(SalesBase):
    def act(self, lead, **body):
        return post("/api/conecto/sales/lead/action/",
                    {"staff_code": self.lani.staff_code, "lead_id": lead.pk, **body})

    def test_note_with_follow_up(self):
        res = self.act(self.b, action="note", kind="call", body="Phoned, call back Friday",
                       next_follow_up="2026-10-09")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertTrue(res.data["success"])
        self.assertEqual(res.data["lead"]["next_follow_up"], "2026-10-09")
        note = self.b.lead_notes.latest("created_at")
        self.assertEqual((note.kind, note.author, note.body), ("call", self.lani, "Phoned, call back Friday"))

    def test_follow_up_set_and_cleared(self):
        self.assertEqual(self.act(self.b, action="follow_up", next_follow_up="2026-10-20").data["lead"]["next_follow_up"],
                         "2026-10-20")
        res = self.act(self.b, action="follow_up", next_follow_up=None)
        self.assertIsNone(res.data["lead"]["next_follow_up"])
        self.assertEqual(self.b.lead_notes.latest("created_at").body, "Follow-up cleared.")

    def test_lost(self):
        res = self.act(self.a, action="lost", lost_reason="price", note="Went with fibre")
        self.assertEqual(res.status_code, 200, res.data)
        self.a.refresh_from_db()
        self.assertEqual((self.a.status, self.a.lost_reason, self.a.next_follow_up), ("lost", "price", None))

    def test_bad_actions(self):
        self.assertEqual(self.act(self.b, action="note", body="").status_code, 400)
        self.assertEqual(self.act(self.b, action="note", kind="sms", body="x").status_code, 400)
        self.assertEqual(self.act(self.b, action="note", body="x", next_follow_up="Friday").status_code, 400)
        self.assertEqual(self.act(self.b, action="follow_up").status_code, 400)
        self.assertEqual(self.act(self.c, action="follow_up", next_follow_up="2026-10-20").status_code, 400)
        self.assertEqual(self.act(self.c, action="lost", lost_reason="price").status_code, 400)
        self.assertEqual(self.act(self.b, action="lost", lost_reason="bored").status_code, 400)
        self.assertEqual(self.act(self.b, action="win").status_code, 400)
        res = self.act(self.d, action="note", body="x")
        self.assertEqual(res.status_code, 404)
        self.assertIn("detail", res.data)


@CONFIGURED
@patch("conecto.client.requests.request")
class SalesEventTests(SalesBase):
    def setUp(self):
        super().setUp()
        self.api = APIClient()
        self.api.force_authenticate(self.boss)

    def test_assigned_by_someone_else(self, request):
        request.return_value = FakeResponse()
        with self.captureOnCommitCallbacks(execute=True):
            res = self.api.post("/api/leads/", {"full_name": "New Person", "assigned_to": self.lani.pk}, format="json")
        self.assertEqual(res.status_code, 201, res.data)
        sent = bodies(request, "salesEvent")
        self.assertEqual(len(sent), 1)
        self.assertEqual((sent[0]["event"], sent[0]["staff_code"], sent[0]["lead_name"]),
                         ("lead_assigned", self.lani.staff_code, "New Person"))
        request.reset_mock()
        with self.captureOnCommitCallbacks(execute=True):
            self.api.patch(f"/api/leads/{res.data['id']}/", {"assigned_to": self.piet.pk}, format="json")
        self.assertEqual([b["staff_code"] for b in bodies(request, "salesEvent")], [self.piet.staff_code])
        request.reset_mock()
        with self.captureOnCommitCallbacks(execute=True):
            self.api.patch(f"/api/leads/{res.data['id']}/", {"city": "Parys"}, format="json")
        self.assertEqual(bodies(request, "salesEvent"), [])

    def test_assigning_yourself_is_quiet(self, request):
        request.return_value = FakeResponse()
        with self.captureOnCommitCallbacks(execute=True):
            self.api.post("/api/leads/", {"full_name": "Mine", "assigned_to": self.boss.pk}, format="json")
        self.assertEqual(bodies(request, "salesEvent"), [])

    def test_scan_result_and_accepted(self, request):
        request.return_value = FakeResponse()
        with self.captureOnCommitCallbacks(execute=True):
            res = self.api.post(f"/api/leads/{self.b.pk}/coverage/", {"method": "electronic", "result": "pass"},
                                format="json")
        self.assertEqual(res.status_code, 200, res.data)
        sent = bodies(request, "salesEvent")
        self.assertEqual([(b["event"], b["result"], b["staff_code"]) for b in sent],
                         [("scan_result", "pass", self.lani.staff_code)])
        request.reset_mock()
        with self.captureOnCommitCallbacks(execute=True):
            self.api.post(f"/api/leads/{self.b.pk}/coverage/", {"method": "electronic", "result": "pass",
                                                                "notes": "same again"}, format="json")
        self.assertEqual(bodies(request, "salesEvent"), [], "unchanged result is not news")
        request.reset_mock()
        with self.captureOnCommitCallbacks(execute=True):
            res = self.api.post(f"/api/leads/{self.b.pk}/accept/", {}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        accepted = [b for b in bodies(request, "salesEvent") if b["event"] == "lead_accepted"]
        self.assertEqual(len(accepted), 1)
        self.b.refresh_from_db()
        self.assertEqual(accepted[0]["client_code"], self.b.customer.customer_id)
        self.assertNotIn("result", accepted[0])


@CONFIGURED
class BackfillPacingTests(TestCase):
    def test_rate_is_capped_and_sleeps_between_sends(self):
        from billing.models import Invoice

        customer = Customer.objects.create(full_name="Jan", customer_id="CUS-000123")
        for _ in range(3):
            Invoice.objects.create(customer=customer, status=Invoice.Status.UNPAID, total=Decimal("100"),
                                   date_due=timezone.localdate())
        with patch("conecto.client.requests.request", return_value=FakeResponse()), \
                patch("time.sleep") as sleep:
            out = StringIO()
            call_command("conecto_backfill_invoices", "--all", "--include-paid", "--apply", "--rate", "50",
                         "--quiet", stdout=out)
        self.assertTrue(sleep.called)
        self.assertEqual(sleep.call_count, 3)
        self.assertTrue(all(0 < c.args[0] <= 0.1 for c in sleep.call_args_list), "capped at 10 a second")
