"""An approved suspension extension's date is the agreed cut-off day (Rudolph
20 Oct 2026: "We have standard suspension dates set up, extension should
accept that there was already communicated with the client and chosen
extension till date should suspend client on selected date" / "Dashboard
should show exact date that the service will be cut").

* Protected before the date; still owing on it -> suspended ON it.
* No new reminder and waiting days first, and not moved off a weekend.
* Never earlier than the normal date for that debt.
* A separate, later debt follows the normal rules again.
* The dashboard and the client tile count from the next run: today if
  today's run hasn't happened yet."""
import datetime
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from billing.models import (
    CustomerBillingConfig, Invoice, RecurringBillingRun, ReminderSettings, Service, Tariff,
)
from billing.recurring import (
    blocking_candidate_services, extension_cutoff, next_run_date, suspension_block_date, upcoming_blocks,
)
from customers.models import Customer

User = get_user_model()
D = datetime.date
TODAY = D(2026, 10, 20)  # Tuesday
SAT = D(2026, 10, 24)


def on(day):
    return mock.patch("django.utils.timezone.localdate", return_value=day)


class Base(TestCase):
    def setUp(self):
        rs = ReminderSettings.load()
        rs.reminder_1_enabled = True
        rs.reminder_2_enabled = True
        rs.save()
        tariff = Tariff.objects.create(name="Wireless 8", price=Decimal("549.00"), tax_rate_pct=Decimal("15.00"))
        self.c = Customer.objects.create(full_name="Trevor Booysen", email="t@example.com",
                                         status=Customer.Status.ACTIVE, balance=Decimal("631.35"))
        Service.objects.create(customer=self.c, tariff=tariff, status=Service.Status.ACTIVE, start_date=D(2026, 1, 1))
        self.g = CustomerBillingConfig.for_customer(self.c)
        self.g.billing_enabled = True
        self.g.auto_create_invoices = False
        self.g.blocking_period_days = 18
        self.g.minimum_balance = Decimal("50.00")
        self.g.reminder_enabled = True
        self.g.reminder_1_day = -14
        self.g.reminder_2_day = -15
        self.g.save()
        self.inv = Invoice.objects.create(customer=self.c, status=Invoice.Status.UNPAID, date_due=D(2026, 10, 1))
        # Issued 01/10 (not the real today the test happens to run on). Never reminded.
        Invoice.objects.filter(pk=self.inv.pk).update(total=Decimal("631.35"), date_issued=D(2026, 10, 1))

    def extend(self, until):
        self.g.suspension_extended_until = until
        self.g.save()


class CutOffDay(Base):
    def test_without_extension_reminder_rule_and_weekend_apply(self):
        # Never reminded: catch-up on the 21st + 3 days = Sat 24th -> Monday.
        self.assertEqual(suspension_block_date(self.c, self.g, D(2026, 10, 21)), D(2026, 10, 26))

    def test_cut_on_the_agreed_saturday(self):
        self.extend(SAT)
        self.assertEqual(blocking_candidate_services(self.c, self.g, D(2026, 10, 23)), [])
        self.assertNotEqual(blocking_candidate_services(self.c, self.g, SAT), [])
        self.assertNotEqual(blocking_candidate_services(self.c, self.g, D(2026, 10, 25)), [], "missed run: next one cuts")
        self.assertEqual(suspension_block_date(self.c, self.g, SAT), SAT)
        with on(TODAY):
            [row] = upcoming_blocks(D(2026, 10, 21), horizon_days=7)
        self.assertEqual((row["block_date"], row["extension_date"], row["days_from_today"]), (SAT, True, 4))

    def test_never_earlier_than_the_normal_date(self):
        Invoice.objects.filter(pk=self.inv.pk).update(date_due=D(2026, 10, 15))
        self.extend(D(2026, 10, 25))
        # 15 Oct + 18 days = 2 Nov.
        self.assertEqual(extension_cutoff(self.c, self.g), D(2026, 11, 2))
        self.assertEqual(blocking_candidate_services(self.c, self.g, D(2026, 10, 25)), [])

    def test_later_separate_debt_follows_normal_rules(self):
        self.extend(D(2026, 10, 10))
        Invoice.objects.filter(pk=self.inv.pk).update(date_due=D(2026, 10, 12))
        self.assertIsNone(extension_cutoff(self.c, self.g))
        # Normal: due 12 Oct + 18 = 30 Oct (Fri), reminder catch-up long before.
        self.assertEqual(suspension_block_date(self.c, self.g, D(2026, 10, 21)), D(2026, 10, 30))

    def test_paid_on_the_day_not_cut(self):
        self.extend(SAT)
        self.c.balance = Decimal("0")
        self.c.save()
        Invoice.objects.filter(pk=self.inv.pk).update(status=Invoice.Status.PAID)
        self.assertEqual(blocking_candidate_services(self.c, self.g, SAT), [])


class NextRun(Base):
    def test_today_until_todays_run_is_done(self):
        with on(TODAY):
            self.assertEqual(next_run_date(), TODAY)
            RecurringBillingRun.objects.create(run_date=TODAY, status=RecurringBillingRun.Status.IN_PROGRESS)
            self.assertEqual(next_run_date(), TODAY, "an unfinished run doesn't count")
            RecurringBillingRun.objects.create(run_date=TODAY, status=RecurringBillingRun.Status.PROCESSED)
            self.assertEqual(next_run_date(), D(2026, 10, 21))

    def test_dashboard_and_tile_show_the_agreed_date(self):
        self.extend(SAT)
        api = APIClient()
        api.force_authenticate(User.objects.create_user(username="boss", password="x", role="admin"))
        with on(TODAY):
            res = api.get("/api/upcoming-blocks/?days=7")
            self.assertEqual(res.status_code, 200, res.data)
            self.assertTrue(res.data["next_run_is_today"])
            self.assertEqual(res.data["run_time"], "15:00")
            [row] = res.data["results"]
            self.assertEqual((row["block_date"], row["extension_date"]), (SAT, True))
            tile = api.get(f"/api/customers/{self.c.pk}/payment-summary/").data
        self.assertEqual((tile["block_date"], tile["days_until_block"], tile["block_is_extension_date"], tile["run_time"]),
                         (SAT, 4, True, "15:00"))

    def test_overdue_client_shows_today_before_the_run(self):
        # Jean Mienie: due 1 Oct, 18 days, reminded on 16 Oct -> due for cut-off.
        Invoice.objects.filter(pk=self.inv.pk).update(last_reminder_run_date=D(2026, 10, 16))
        with on(TODAY):
            [row] = upcoming_blocks(next_run_date(), horizon_days=7)
        self.assertEqual((row["block_date"], row["days_from_today"]), (TODAY, 0))


class RequestDate(Base):
    def test_today_refused(self):
        api = APIClient()
        api.force_authenticate(User.objects.create_user(username="liesi", password="x", role="management",
                                                         allowed_sections=["customers", "finance"]))
        with on(TODAY):
            res = api.post("/api/suspension-extension-requests/",
                           {"customer": self.c.pk, "extend_until": "2026-10-20", "reason": "x"}, format="json")
            self.assertEqual(res.status_code, 400, res.data)
            res = api.post("/api/suspension-extension-requests/",
                           {"customer": self.c.pk, "extend_until": "2026-10-24", "reason": "Pays Saturday"}, format="json")
            self.assertEqual(res.status_code, 201, res.data)
