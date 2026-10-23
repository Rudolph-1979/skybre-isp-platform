"""Service cancellation (#203, Rudolph 20 Oct 2026): one calendar month's
notice, unless the client can't be serviced on this line any more and moves
to a new service; pro rata credit (for Management approval) for days already
invoiced after the end date; the last period not yet invoiced is billed pro
rata by the recurring run."""
import datetime
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from billing import service_cancel as sc
from billing.gate_testing import ApprovalGateOpenMixin
from billing.invoice_lines import customer_invoice_lines
from billing.models import (
    CreditRequest, CustomerBillingConfig, Invoice, InvoiceItem, RecurringBillingRun, Service,
    ServiceCancellation, Tariff,
)
from billing.recurring import run_recurring_billing
from customers.models import Customer

User = get_user_model()
D = datetime.date
TODAY = D(2026, 10, 20)


def at_today():
    return mock.patch("django.utils.timezone.localdate", return_value=TODAY)


class Base(TestCase):
    def setUp(self):
        self.wifi = Tariff.objects.create(name="Skybre Wireless 6 Mbps", price=Decimal("349.00"), tax_rate_pct=Decimal("15.00"),
                                          speed_download_kbps=6144, speed_upload_kbps=6144)
        self.vuma = Tariff.objects.create(name="Vuma BF 30/30", price=Decimal("449.00"), tax_rate_pct=Decimal("15.00"),
                                          speed_download_kbps=30720, speed_upload_kbps=30720)
        self.c = Customer.objects.create(full_name="Vossie Voster", status=Customer.Status.ACTIVE)
        cfg = CustomerBillingConfig.for_customer(self.c)
        cfg.billing_day = 1
        cfg.next_billing_date = None
        cfg.use_date_of_customer_creation = False
        cfg.payment_period = "monthly"
        cfg.save()
        self.old = Service.objects.create(customer=self.c, tariff=self.wifi, status=Service.Status.ACTIVE,
                                          start_date=D(2026, 9, 1), radius_username="krd_vossie")
        self.new = Service.objects.create(customer=self.c, tariff=self.vuma, status=Service.Status.ACTIVE,
                                          start_date=D(2026, 10, 19), radius_username="svf_krd_vossie")
        self.boss = User.objects.create_user(username="boss", password="x", role="management")

    def run_invoice(self, service, run_date, price=None):
        run = RecurringBillingRun.objects.create(run_date=run_date)
        inv = Invoice.objects.create(customer=self.c, status=Invoice.Status.UNPAID, date_due=run_date, created_by_run=run)
        InvoiceItem.objects.create(invoice=inv, item_type=InvoiceItem.ItemType.TARIFF, tariff=service.tariff, service=service,
                                   description=service.tariff.name, quantity=1, unit_price=price or service.tariff.price,
                                   tax_rate_pct=Decimal("15.00"))
        inv.recalc_totals()
        return inv


class NoticeTests(Base):
    def test_one_calendar_month(self):
        self.assertEqual(sc.notice_end_date(D(2026, 10, 20)), D(2026, 11, 20))
        self.assertEqual(sc.notice_end_date(D(2027, 1, 31)), D(2027, 2, 28))
        with at_today():
            r = sc.plan(self.old, "notice")
        self.assertEqual((r["end_date"], r["last_day"]), (D(2026, 11, 20), D(2026, 11, 19)))
        self.assertEqual(r["credits"], [])
        # November not invoiced yet: billed 1-19 Nov by the run.
        self.assertEqual(r["last_period"], {"from": D(2026, 11, 1), "to": D(2026, 11, 19), "days": 19, "period_days": 30})

    def test_earlier_end_refused_later_allowed(self):
        with at_today():
            with self.assertRaisesMessage(sc.CancelError, "One month's notice is required"):
                sc.plan(self.old, "notice", end_date=D(2026, 11, 1))
            self.assertEqual(sc.plan(self.old, "notice", end_date=D(2026, 12, 1))["end_date"], D(2026, 12, 1))
            with self.assertRaises(sc.CancelError):
                sc.plan(self.old, "notice", notice_date=D(2026, 10, 21))

    def test_backdated_notice_credits_the_invoiced_month(self):
        oct_inv = self.run_invoice(self.old, D(2026, 10, 1))
        with at_today():
            r = sc.plan(self.old, "notice", notice_date=D(2026, 9, 25))
        self.assertEqual(r["end_date"], D(2026, 10, 25))
        [line] = r["credits"]
        self.assertEqual(line["invoice"], oct_inv)
        self.assertEqual((line["days"], line["period_days"]), (7, 31))
        # 349 + 15% = 401.35; 7/31 of it.
        self.assertEqual(line["amount"], Decimal("90.63"))


class CannotServiceTests(Base):
    def test_needs_the_new_service(self):
        with at_today():
            with self.assertRaisesMessage(sc.CancelError, "new service"):
                sc.plan(self.old, "cannot_service")
            with self.assertRaises(sc.CancelError):
                sc.plan(self.old, "cannot_service", replacement=self.old)

    def test_ends_when_new_one_started_and_credits_rest_of_month(self):
        oct_inv = self.run_invoice(self.old, D(2026, 10, 1))
        with at_today():
            r = sc.plan(self.old, "cannot_service", replacement=self.new)
            self.assertEqual(r["end_date"], D(2026, 10, 19))
            self.assertTrue(r["ends_now"])
            cancel = sc.commit(self.old, r, "Moved to Vuma fibre", self.boss)
        self.old.refresh_from_db()
        self.assertEqual(self.old.status, Service.Status.TERMINATED)
        self.assertEqual(self.old.end_date, D(2026, 10, 19))
        self.c.refresh_from_db()
        self.assertEqual(self.c.status, Customer.Status.ACTIVE, "still has the Vuma line")
        [credit] = cancel.credit_requests.all()
        self.assertEqual((credit.status, credit.invoice, credit.amount), (CreditRequest.Status.PENDING, oct_inv, Decimal("168.31")))
        self.assertIn("13 of 31 days", credit.reason)
        self.assertEqual(cancel.replacement_service, self.new)

    def test_pro_rata_invoice_period_used(self):
        inv = Invoice.objects.create(customer=self.c, status=Invoice.Status.UNPAID, date_due=TODAY)
        InvoiceItem.objects.create(invoice=inv, item_type="tariff", tariff=self.vuma, service=self.new, description="pro rata",
                                   quantity=1, unit_price=Decimal("188.29"), tax_rate_pct=Decimal("15.00"),
                                   period_start=D(2026, 10, 19), period_end=D(2026, 10, 31))
        with at_today():
            r = sc.plan(self.new, "cannot_service", replacement=self.old, end_date=D(2026, 10, 25))
        [line] = r["credits"]
        self.assertEqual((line["days"], line["period_days"]), (7, 13))

    def test_future_end_date_not_ended_yet(self):
        with at_today():
            r = sc.plan(self.old, "cannot_service", replacement=self.new, end_date=D(2026, 10, 25))
            sc.commit(self.old, r, "Moving", self.boss)
        self.old.refresh_from_db()
        self.assertEqual((self.old.status, self.old.end_date), (Service.Status.ACTIVE, D(2026, 10, 25)))


class WithdrawTests(Base):
    def test_withdraw_restores_and_rejects_pending_credit(self):
        self.run_invoice(self.old, D(2026, 10, 1))
        with at_today():
            r = sc.plan(self.old, "notice", notice_date=D(2026, 9, 25))
            cancel = sc.commit(self.old, r, "Notice by email", self.boss)
            sc.withdraw(cancel, self.boss)
        self.old.refresh_from_db()
        self.assertIsNone(self.old.end_date)
        self.assertEqual(cancel.credit_requests.get().status, CreditRequest.Status.REJECTED)
        with self.assertRaises(sc.CancelError):
            sc.withdraw(cancel, self.boss)

    def test_only_one_booked(self):
        with at_today():
            sc.commit(self.old, sc.plan(self.old, "notice"), "x", self.boss)
            with self.assertRaisesMessage(sc.CancelError, "already has a cancellation"):
                sc.commit(self.old, sc.plan(self.old, "notice"), "x", self.boss)


class RunBillsLastPeriodProRata(ApprovalGateOpenMixin, Base):
    def test_lines(self):
        self.old.end_date = D(2026, 11, 20)
        self.old.save()
        lines = customer_invoice_lines(self.c, D(2026, 11, 1))
        wifi = [l for l in lines if l["service"] == self.old][0]
        self.assertEqual(wifi["unit_price"], Decimal("221.03"))  # 349 x 19/30
        self.assertIn("pro rata 01/11/2026 to 19/11/2026 (19 of 30 days)", wifi["description"])
        vuma = [l for l in lines if l["service"] == self.new][0]
        self.assertEqual(vuma["unit_price"], Decimal("449.00"))
        # Ends on the billing date: not billed at all, as before.
        self.old.end_date = D(2026, 11, 1)
        self.old.save()
        self.assertFalse([l for l in customer_invoice_lines(self.c, D(2026, 11, 1)) if l["service"] == self.old])
        # Ends after the period: full price.
        self.old.end_date = D(2026, 12, 5)
        self.old.save()
        wifi = [l for l in customer_invoice_lines(self.c, D(2026, 11, 1)) if l["service"] == self.old][0]
        self.assertEqual(wifi["unit_price"], Decimal("349.00"))

    def test_discount_follows(self):
        self.old.end_date = D(2026, 11, 16)
        self.old.discount_type = "fixed"
        self.old.discount_value = Decimal("30.00")
        self.old.save()
        lines = [l for l in customer_invoice_lines(self.c, D(2026, 11, 1)) if l["service"] == self.old]
        self.assertEqual([l["kind"] for l in lines], ["tariff", "discount"])
        self.assertEqual(lines[0]["unit_price"], Decimal("174.50"))  # 349 x 15/30
        self.assertEqual(lines[1]["unit_price"], Decimal("-15.00"))

    def test_real_run(self):
        self.old.end_date = D(2026, 11, 20)
        self.old.save()
        cfg = CustomerBillingConfig.for_customer(self.c)
        cfg.billing_enabled = True
        cfg.next_billing_date = D(2026, 11, 1)
        cfg.save()
        with mock.patch("django.utils.timezone.localdate", return_value=D(2026, 11, 1)):
            run_recurring_billing(D(2026, 11, 1), commit=True)
        inv = Invoice.objects.get(customer=self.c, created_by_run__isnull=False)
        item = inv.items.get(service=self.old)
        self.assertEqual(item.unit_price, Decimal("221.03"))
        # And cancelling later finds nothing more to credit.
        with mock.patch("django.utils.timezone.localdate", return_value=D(2026, 11, 2)):
            self.assertEqual(sc.credit_lines(self.old, D(2026, 11, 20))[0], [])


class ApiTests(Base):
    def api(self, role):
        client = APIClient()
        client.force_authenticate(User.objects.create_user(username=role, password="x", role=role,
                                                           allowed_sections=["customers", "services"]))
        return client

    def test_preview_then_commit(self):
        client = self.api("admin")
        with at_today():
            res = client.post(f"/api/services/{self.old.pk}/cancel/", {"kind": "notice"}, format="json")
            self.assertEqual(res.status_code, 200, res.data)
            self.assertEqual((res.data["end_date"], res.data["committed"]), ("2026-11-20", False))
            self.assertFalse(ServiceCancellation.objects.exists())
            res = client.post(f"/api/services/{self.old.pk}/cancel/", {"kind": "notice", "commit": "1"}, format="json")
            self.assertEqual(res.status_code, 400, "reason required")
            res = client.post(f"/api/services/{self.old.pk}/cancel/",
                              {"kind": "notice", "commit": "1", "reason": "Emigrating"}, format="json")
            self.assertEqual(res.status_code, 201, res.data)
            row = client.get(f"/api/services/{self.old.pk}/").data
            self.assertEqual(row["cancellation"]["end_date"], D(2026, 11, 20))
            self.assertEqual(row["cancellation"]["reason"], "Emigrating")
            res = client.post(f"/api/services/{self.old.pk}/withdraw-cancellation/")
            self.assertEqual(res.status_code, 200, res.data)
            self.assertIsNone(client.get(f"/api/services/{self.old.pk}/").data["cancellation"])

    def test_cannot_service_via_api(self):
        with at_today():
            res = self.api("management").post(
                f"/api/services/{self.old.pk}/cancel/",
                {"kind": "cannot_service", "replacement_service": self.new.pk, "commit": "1", "reason": "Moved to Vuma"},
                format="json",
            )
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["replacement_name"], "Vuma BF 30/30")

    def test_other_staff_refused(self):
        for role in ("support", "accounts"):
            res = self.api(role).post(f"/api/services/{self.old.pk}/cancel/", {"kind": "notice"}, format="json")
            self.assertEqual(res.status_code, 403, role)
