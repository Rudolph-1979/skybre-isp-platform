"""The suspension countdown starts when the client's BALANCE went over the
minimum balance (21 Oct 2026, Rudolph: "instead of checking invoices check
customer ballance" -- Aubrey Pie SW1948: R14 owed for weeks, under the R50
minimum; a new invoice on 20/10 took him to R668.02 and the 15:00 run cut him
off at once because the R14 invoice was long past its blocking period).
Extensions are not affected."""
import datetime
from decimal import Decimal
from unittest import mock

from django.test import TestCase

from billing.models import CustomerBillingConfig, Invoice, Service, Tariff
from billing.reactivation import is_clear
from billing.recurring import (
    _FALLBACK, balance_clock_start, blocking_candidate_services, suspension_block_date, upcoming_blocks,
)
from customers.models import Customer

D = datetime.date
TUE = D(2026, 10, 20)


class BalanceClockTests(TestCase):
    def setUp(self):
        tariff = Tariff.objects.create(name="Wireless 5", price=Decimal("249.00"), tax_rate_pct=Decimal("15.00"))
        self.c = Customer.objects.create(full_name="Aubrey Pie", status=Customer.Status.ACTIVE, balance=Decimal("14.00"))
        Service.objects.create(customer=self.c, tariff=tariff, status=Service.Status.ACTIVE, start_date=D(2026, 1, 1))
        self.g = CustomerBillingConfig.for_customer(self.c)
        self.g.billing_enabled = True
        self.g.auto_create_invoices = False
        self.g.blocking_period_days = 4
        self.g.minimum_balance = Decimal("50.00")
        self.g.reminder_enabled = False
        self.g.save()
        self.invoice(D(2026, 9, 1), D(2026, 9, 1), "14.00")

    def invoice(self, issued, due, total):
        inv = Invoice.objects.create(customer=self.c, status=Invoice.Status.UNPAID, date_due=due)
        Invoice.objects.filter(pk=inv.pk).update(total=Decimal(total), date_issued=issued)
        return inv

    def go_over(self, due=TUE):
        self.invoice(TUE, due, "654.02")
        self.c.balance = Decimal("668.02")
        self.c.save()

    def test_under_minimum(self):
        self.assertIsNone(balance_clock_start(self.c, self.g))
        self.assertEqual(blocking_candidate_services(self.c, self.g, TUE), [])

    def test_counts_from_the_day_he_went_over(self):
        self.go_over()
        self.assertEqual(balance_clock_start(self.c, self.g), TUE)
        self.assertEqual(blocking_candidate_services(self.c, self.g, TUE), [])
        # 20/10 + 4 days = Sat 24/10 -> Monday 26/10.
        self.assertEqual(suspension_block_date(self.c, self.g, TUE), D(2026, 10, 26))
        self.assertNotEqual(blocking_candidate_services(self.c, self.g, D(2026, 10, 26)), [])
        with mock.patch("django.utils.timezone.localdate", return_value=TUE):
            rows = upcoming_blocks(D(2026, 10, 21), horizon_days=10, customers=Customer.objects.filter(pk=self.c.pk))
        self.assertEqual([r["block_date"] for r in rows], [D(2026, 10, 26)])

    def test_payment_terms_still_count(self):
        self.go_over(due=D(2026, 10, 27))
        self.assertEqual(balance_clock_start(self.c, self.g), D(2026, 10, 27))

    def test_staying_over_keeps_the_original_start(self):
        Invoice.objects.update(total=Decimal("100.00"))
        self.c.balance = Decimal("100.00")
        self.c.save()
        self.go_over()
        self.c.balance = Decimal("754.02")
        self.c.save()
        self.assertEqual(balance_clock_start(self.c, self.g), D(2026, 9, 1))
        self.assertNotEqual(blocking_candidate_services(self.c, self.g, TUE), [])

    def test_reconnected_by_hand_counts_as_clear_until_the_new_date(self):
        self.go_over()
        with mock.patch("django.utils.timezone.localdate", return_value=D(2026, 10, 21)):
            self.assertTrue(is_clear(self.c, self.g))

    def test_account_that_does_not_add_up_uses_the_old_rule(self):
        self.go_over()
        self.c.balance = Decimal("900.00")   # stored balance disagrees with the records
        self.c.save()
        self.assertIs(balance_clock_start(self.c, self.g), _FALLBACK)
        self.assertNotEqual(blocking_candidate_services(self.c, self.g, TUE), [])

    def test_extension_date_unchanged(self):
        self.go_over()
        self.g.suspension_extended_until = D(2026, 10, 21)
        self.g.save()
        self.assertEqual(suspension_block_date(self.c, self.g, D(2026, 10, 21)), D(2026, 10, 21))
        self.assertNotEqual(blocking_candidate_services(self.c, self.g, D(2026, 10, 21)), [])
