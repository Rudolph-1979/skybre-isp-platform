"""Pausing a service (#211, Rudolph 21 Oct 2026): the line is off while
paused (RADIUS reject), the paused days are not billed (pro rata; days
already invoiced -> credit request for Management), an optional resume date
switches it back on that morning, Admin / Management only."""
import datetime
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from billing import service_pause as sp
from billing.gate_testing import ApprovalGateOpenMixin
from billing.invoice_lines import customer_invoice_lines
from billing.models import (
    CreditRequest, CustomerBillingConfig, Invoice, InvoiceItem, RecurringBillingRun, ReminderSettings, Service,
    ServicePause, Tariff,
)
from billing.recurring import blocking_candidate_services, run_recurring_billing
from billing.scheduled_changes import apply_due_scheduled_changes
from customers.models import Customer
from radiusauth.models import RadCheck

User = get_user_model()
D = datetime.date
TODAY = D(2026, 10, 21)


def on(day):
    return mock.patch("django.utils.timezone.localdate", return_value=day)


class Base(TestCase):
    def setUp(self):
        self.wifi = Tariff.objects.create(name="Skybre Wireless 6 Mbps", price=Decimal("349.00"), tax_rate_pct=Decimal("15.00"),
                                          speed_download_kbps=6144, speed_upload_kbps=6144)
        self.c = Customer.objects.create(full_name="Pauline Paws", status=Customer.Status.ACTIVE)
        cfg = CustomerBillingConfig.for_customer(self.c)
        cfg.billing_day = 1
        cfg.next_billing_date = None
        cfg.use_date_of_customer_creation = False
        cfg.payment_period = "monthly"
        cfg.save()
        self.s = Service.objects.create(customer=self.c, tariff=self.wifi, status=Service.Status.ACTIVE,
                                        start_date=D(2026, 9, 1), radius_username="krd_pauline",
                                        radius_password="secret")
        self.boss = User.objects.create_user(username="boss", password="x", role="management")

    def run_invoice(self, run_date):
        run = RecurringBillingRun.objects.create(run_date=run_date, status=RecurringBillingRun.Status.PROCESSED)
        inv = Invoice.objects.create(customer=self.c, status=Invoice.Status.UNPAID, date_due=run_date, created_by_run=run)
        InvoiceItem.objects.create(invoice=inv, item_type=InvoiceItem.ItemType.TARIFF, tariff=self.wifi, service=self.s,
                                   description=self.wifi.name, quantity=1, unit_price=self.wifi.price,
                                   tax_rate_pct=Decimal("15.00"))
        inv.recalc_totals()
        return inv

    def pause(self, pause_from=None, resume_on=None, reason="Away for a month", today=TODAY):
        with on(today):
            result = sp.plan(self.s, pause_from=pause_from, resume_on=resume_on)
            pause = sp.commit(self.s, result, reason, self.boss)
        self.s.refresh_from_db()
        return pause, result

    def lines(self, run_date):
        return [l for l in customer_invoice_lines(self.c, run_date) if l["service"] == self.s]


class PauseNow(Base):
    def test_line_off_and_login_refused(self):
        pause, result = self.pause()
        self.assertTrue(result["starts_now"])
        self.assertEqual(self.s.status, Service.Status.PAUSED)
        self.assertEqual(pause.status, ServicePause.Status.ACTIVE)
        self.assertTrue(RadCheck.objects.filter(username="krd_pauline", attribute="Auth-Type", value="Reject").exists())
        self.assertFalse(RadCheck.objects.filter(username="krd_pauline", attribute="Cleartext-Password").exists())
        self.c.refresh_from_db()
        self.assertEqual(self.c.status, Customer.Status.ACTIVE, "pausing a line never changes the client")

    def test_rules(self):
        with on(TODAY):
            with self.assertRaisesMessage(sp.PauseError, "past"):
                sp.plan(self.s, pause_from=D(2026, 10, 20))
            with self.assertRaisesMessage(sp.PauseError, "after the first paused day"):
                sp.plan(self.s, pause_from=D(2026, 10, 25), resume_on=D(2026, 10, 25))
            with self.assertRaisesMessage(sp.PauseError, "reason"):
                sp.commit(self.s, sp.plan(self.s), " ", self.boss)
        self.pause()
        with on(TODAY), self.assertRaisesMessage(sp.PauseError, "already paused"):
            sp.plan(self.s)

    def test_suspended_line_cannot_be_paused(self):
        Service.objects.filter(pk=self.s.pk).update(status=Service.Status.SUSPENDED)
        self.s.refresh_from_db()
        with on(TODAY), self.assertRaisesMessage(sp.PauseError, "suspended"):
            sp.plan(self.s)


class Credit(Base):
    def test_open_pause_credits_rest_of_invoiced_month(self):
        oct_inv = self.run_invoice(D(2026, 10, 1))
        pause, result = self.pause()
        [line] = result["credits"]
        self.assertEqual((line["invoice"], line["days"], line["period_days"]), (oct_inv, 11, 31))
        self.assertEqual(line["amount"], Decimal("142.41"))  # 401.35 x 11/31
        [credit] = pause.credit_requests.all()
        self.assertEqual((credit.status, credit.amount), (CreditRequest.Status.PENDING, Decimal("142.41")))
        self.assertIn("11 of 31 days paused", credit.reason)

    def test_resume_date_limits_credit(self):
        self.run_invoice(D(2026, 10, 1))
        _pause, result = self.pause(resume_on=D(2026, 10, 26))
        self.assertEqual(result["credits"][0]["amount"], Decimal("64.73"))  # 401.35 x 5/31

    def test_early_resume_recalculates_pending_credit(self):
        self.run_invoice(D(2026, 10, 1))
        pause, _ = self.pause()
        old = pause.credit_requests.get()
        with on(D(2026, 10, 26)):
            result = sp.resume(pause, self.boss)
        old.refresh_from_db()
        self.assertEqual(old.status, CreditRequest.Status.REJECTED)
        [new] = result["credits"]
        self.assertEqual(new.amount, Decimal("64.73"))  # 21-25 Oct
        self.s.refresh_from_db()
        self.assertEqual(self.s.status, Service.Status.ACTIVE)
        pause.refresh_from_db()
        self.assertEqual((pause.status, pause.resumed_on), (ServicePause.Status.ENDED, D(2026, 10, 26)))
        self.assertIsNone(result["unbilled"], "October was invoiced before the pause: the credit covers it")

    def test_approved_credit_is_kept(self):
        self.run_invoice(D(2026, 10, 1))
        pause, _ = self.pause()
        CreditRequest.objects.filter(pk=pause.credit_requests.get().pk).update(status=CreditRequest.Status.APPROVED)
        with on(D(2026, 10, 26)):
            result = sp.resume(pause, self.boss)
        self.assertEqual(result["credits"], [])
        self.assertEqual(len(result["kept_credits"]), 1)


class Scheduled(Base):
    def test_booked_pause_starts_and_resumes_by_itself(self):
        pause, result = self.pause(pause_from=D(2026, 10, 25), resume_on=D(2026, 11, 5))
        self.assertFalse(result["starts_now"])
        self.assertEqual((self.s.status, pause.status), (Service.Status.ACTIVE, ServicePause.Status.SCHEDULED))
        done = apply_due_scheduled_changes(as_of=D(2026, 10, 24))
        self.assertEqual(done["paused"], [])
        done = apply_due_scheduled_changes(as_of=D(2026, 10, 25))
        self.assertEqual([s.pk for s in done["paused"]], [self.s.pk])
        self.s.refresh_from_db()
        self.assertEqual(self.s.status, Service.Status.PAUSED)
        done = apply_due_scheduled_changes(as_of=D(2026, 11, 5))
        self.assertEqual([s.pk for s in done["resumed"]], [self.s.pk])
        self.s.refresh_from_db()
        pause.refresh_from_db()
        self.assertEqual(self.s.status, Service.Status.ACTIVE)
        self.assertEqual((pause.status, pause.resumed_on), (ServicePause.Status.ENDED, D(2026, 11, 5)))

    def test_resume_before_start_withdraws(self):
        self.run_invoice(D(2026, 10, 1))
        pause, _ = self.pause(pause_from=D(2026, 10, 25))
        credit = pause.credit_requests.get()
        with on(TODAY):
            result = sp.resume(pause, self.boss)
        self.assertTrue(result["withdrawn"])
        pause.refresh_from_db()
        credit.refresh_from_db()
        self.assertEqual(pause.status, ServicePause.Status.WITHDRAWN)
        self.assertEqual(credit.status, CreditRequest.Status.REJECTED)
        self.s.refresh_from_db()
        self.assertEqual(self.s.status, Service.Status.ACTIVE)
        self.assertEqual(self.lines(D(2026, 11, 1))[0]["unit_price"], Decimal("349.00"))

    def test_client_suspended_while_paused(self):
        self.pause(resume_on=D(2026, 11, 5))
        self.c.status = Customer.Status.SUSPENDED
        self.c.save()
        self.s.refresh_from_db()
        self.assertEqual(self.s.status, Service.Status.PAUSED, "the cascade only suspends active lines")
        apply_due_scheduled_changes(as_of=D(2026, 11, 5))
        self.s.refresh_from_db()
        self.assertEqual((self.s.status, self.s.auto_suspended_with_customer), (Service.Status.SUSPENDED, True))
        self.c.status = Customer.Status.ACTIVE
        self.c.save()
        self.s.refresh_from_db()
        self.assertEqual(self.s.status, Service.Status.ACTIVE)


class RunLines(ApprovalGateOpenMixin, Base):
    def test_pro_rata_both_ends(self):
        self.pause(pause_from=D(2026, 10, 25), resume_on=D(2026, 11, 5))
        [nov] = self.lines(D(2026, 11, 1))
        self.assertEqual(nov["unit_price"], Decimal("302.47"))  # 349 x 26/30
        self.assertIn("paused 01/11/2026 to 04/11/2026 (26 of 30 days)", nov["description"])
        self.assertEqual((nov["period_start"], nov["period_end"]), (D(2026, 11, 1), D(2026, 11, 30)))
        [octo] = self.lines(D(2026, 10, 1))
        self.assertEqual(octo["unit_price"], Decimal("270.19"))  # 349 x 24/31
        self.assertIn("paused 25/10/2026 to 31/10/2026", octo["description"])
        [dec] = self.lines(D(2026, 12, 1))
        self.assertEqual(dec["unit_price"], Decimal("349.00"))

    def test_pause_inside_one_period(self):
        self.pause(pause_from=D(2026, 11, 10), resume_on=D(2026, 11, 15))
        [nov] = self.lines(D(2026, 11, 1))
        self.assertEqual(nov["unit_price"], Decimal("290.83"))  # 349 x 25/30

    def test_open_pause_not_billed(self):
        self.pause()
        self.assertEqual(self.lines(D(2026, 11, 1)), [])
        self.assertEqual(self.lines(D(2027, 1, 1)), [])

    def test_real_run_skips_and_bills_pro_rata(self):
        cfg = CustomerBillingConfig.for_customer(self.c)
        cfg.billing_enabled = True
        cfg.next_billing_date = D(2026, 11, 1)
        cfg.save()
        self.pause(pause_from=D(2026, 10, 25), resume_on=D(2026, 11, 5))
        with on(D(2026, 11, 1)):
            run_recurring_billing(D(2026, 11, 1), commit=True)
        self.s.refresh_from_db()
        self.assertEqual(self.s.status, Service.Status.PAUSED, "the run's scheduled changes started the booked pause")
        item = InvoiceItem.objects.get(service=self.s, invoice__created_by_run__isnull=False)
        self.assertEqual(item.unit_price, Decimal("302.47"))

    def test_unbilled_days_after_early_resume(self):
        pause, _ = self.pause()
        RecurringBillingRun.objects.create(run_date=D(2026, 11, 1), status=RecurringBillingRun.Status.PROCESSED)
        with on(D(2026, 11, 10)):
            result = sp.resume(pause, self.boss)
        self.assertEqual(result["unbilled"], {
            "from": D(2026, 11, 10), "to": D(2026, 11, 30), "days": 21, "period_days": 30,
            "amount_incl_vat": Decimal("280.95"),  # 401.35 x 21/30
        })


class NotBlocked(Base):
    def setUp(self):
        super().setUp()
        rs = ReminderSettings.load()
        rs.reminder_1_enabled = True
        rs.save()
        self.other = Service.objects.create(customer=self.c, tariff=self.wifi, status=Service.Status.ACTIVE,
                                            start_date=D(2026, 1, 1))
        Customer.objects.filter(pk=self.c.pk).update(balance=Decimal("631.35"))
        self.c.refresh_from_db()
        self.g = CustomerBillingConfig.for_customer(self.c)
        self.g.billing_enabled = True
        self.g.auto_create_invoices = False
        self.g.blocking_period_days = 18
        self.g.minimum_balance = Decimal("50.00")
        self.g.reminder_enabled = True
        self.g.reminder_1_day = -14
        self.g.save()
        inv = Invoice.objects.create(customer=self.c, status=Invoice.Status.UNPAID, date_due=D(2026, 10, 1))
        Invoice.objects.filter(pk=inv.pk).update(total=Decimal("631.35"), date_issued=D(2026, 10, 1),
                                                 last_reminder_run_date=D(2026, 10, 16))

    def test_paused_line_is_not_a_candidate(self):
        before = blocking_candidate_services(self.c, self.g, D(2026, 10, 20))
        self.assertEqual({s.pk for s in before}, {self.s.pk, self.other.pk})
        self.pause(today=D(2026, 10, 20))
        after = blocking_candidate_services(self.c, self.g, D(2026, 10, 20))
        self.assertEqual([s.pk for s in after], [self.other.pk])


class Api(Base):
    def api(self, role):
        client = APIClient()
        client.force_authenticate(User.objects.create_user(username=role, password="x", role=role,
                                                           allowed_sections=["customers", "services"]))
        return client

    def test_preview_commit_resume(self):
        client = self.api("admin")
        with on(TODAY):
            res = client.post(f"/api/services/{self.s.pk}/pause/", {"resume_on": "2026-11-05"}, format="json")
            self.assertEqual(res.status_code, 200, res.data)
            self.assertEqual((res.data["pause_from"], res.data["starts_now"], res.data["committed"]),
                             ("2026-10-21", True, False))
            self.assertFalse(ServicePause.objects.exists())
            res = client.post(f"/api/services/{self.s.pk}/pause/", {"commit": "1"}, format="json")
            self.assertEqual(res.status_code, 400, "reason required")
            res = client.post(f"/api/services/{self.s.pk}/pause/",
                              {"resume_on": "2026-11-05", "commit": "1", "reason": "Overseas"}, format="json")
            self.assertEqual(res.status_code, 201, res.data)
            row = client.get(f"/api/services/{self.s.pk}/").data
            self.assertEqual((row["status"], row["pause"]["resume_on"], row["pause"]["reason"]),
                             ("paused", D(2026, 11, 5), "Overseas"))
            res = client.post(f"/api/services/{self.s.pk}/resume/")
            self.assertEqual(res.status_code, 200, res.data)
            self.assertEqual(res.data["line_status"], "active")
            row = client.get(f"/api/services/{self.s.pk}/").data
            self.assertEqual((row["status"], row["pause"]), ("active", None))
            self.assertEqual(client.post(f"/api/services/{self.s.pk}/resume/").status_code, 400)

    def test_other_staff_refused(self):
        for role in ("support", "accounts"):
            client = self.api(role)
            self.assertEqual(client.post(f"/api/services/{self.s.pk}/pause/", {}, format="json").status_code, 403, role)
            self.assertEqual(client.post(f"/api/services/{self.s.pk}/resume/").status_code, 403, role)

    def test_status_not_picked_on_the_edit_form(self):
        client = self.api("admin")
        res = client.patch(f"/api/services/{self.s.pk}/", {"status": "paused"}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertIn("Use Pause", str(res.data))
        self.pause()
        res = client.patch(f"/api/services/{self.s.pk}/", {"status": "active"}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertIn("Use Resume", str(res.data))
        res = client.patch(f"/api/services/{self.s.pk}/", {"status": "paused", "access_detail": "Sector B"}, format="json")
        self.assertEqual(res.status_code, 200, res.data)

    def test_status_filter(self):
        self.pause()
        res = self.api("admin").get("/api/services/?status=paused")
        self.assertEqual([r["id"] for r in res.data["results"]], [self.s.pk])
