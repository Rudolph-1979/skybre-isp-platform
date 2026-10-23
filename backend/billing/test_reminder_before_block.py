"""No reminder, no block (George, 12 Oct 2026).

Lefu Samuel Motaung (SWF2550) was due to be suspended with no reminder ever
sent: his invoices arrived by import after their reminder days. A client who
can be reminded is now not suspended until a reminder about their current
debt has gone out, plus the usual gap; missed reminder days get one catch-up
reminder on the next run. Clients who cannot be reminded block as before.
"""
import datetime
from decimal import Decimal

from django.test import TestCase

from customers.models import Customer

from .models import CustomerBillingConfig, Invoice, ReminderSettings, Service, Tariff
from .recurring import (
    _process_reminders, blocking_candidate_services, reminder_gap_days, suspension_block_date, upcoming_blocks,
)

D = datetime.date(2026, 10, 5)          # a Monday; D + 4 = Fri 9, D + 15 = Tue 20, D + 16 = Wed 21


def day(n):
    return D + datetime.timedelta(days=n)


class ReminderBeforeBlockTests(TestCase):
    def setUp(self):
        rs = ReminderSettings.load()
        rs.reminder_1_enabled = rs.reminder_2_enabled = True
        rs.save()
        self.rs = rs
        self.tariff = Tariff.objects.create(name="W 4", price=Decimal("500.00"), tax_rate_pct=Decimal("15"))

    def _client(self, name="Lefu Motaung", email="lefu@example.com", reminders=True, invoices=1):
        customer = Customer.objects.create(full_name=name, email=email, status=Customer.Status.ACTIVE,
                                           balance=Decimal("575.00") * invoices)
        Service.objects.create(customer=customer, tariff=self.tariff, status=Service.Status.ACTIVE)
        config = CustomerBillingConfig.for_customer(customer)
        config.billing_enabled = True
        config.auto_create_invoices = False
        config.next_billing_date = None
        config.reminder_enabled = reminders
        config.reminder_1_day, config.reminder_2_day = -2, -3
        config.blocking_period_days = 4
        config.minimum_balance = Decimal("50.00")
        config.save()
        for k in range(invoices):
            Invoice.objects.create(customer=customer, status=Invoice.Status.UNPAID, date_due=day(-k),
                                   date_issued=day(-k), total=Decimal("575.00"))
        return customer, config

    def _remind(self, customer, config, on):
        """What the engine does on `on`: decide, then record a successful send."""
        sent = _process_reminders(customer, config, self.rs, on, commit=False)
        if sent:
            # The engine chases the oldest owed invoice; so does this.
            invoice = customer.invoices.order_by("date_due", "pk").first()
            Invoice.objects.filter(pk=invoice.pk).update(last_reminder_run_date=on)
        return sent

    def _blocked(self, customer, config, on):
        return bool(blocking_candidate_services(customer, config, on))

    def test_gap_is_from_the_last_scheduled_reminder(self):
        customer, config = self._client()
        self.assertEqual(reminder_gap_days(config, self.rs), 1)          # reminders +2, +3; cut-off +4

    def test_the_normal_timeline_is_unchanged(self):
        customer, config = self._client()
        self.assertEqual([n for n in range(6) if self._remind(customer, config, day(n))], [2, 3])
        self.assertFalse(self._blocked(customer, config, day(3)))
        self.assertTrue(self._blocked(customer, config, day(4)))

    def test_missed_reminders_get_a_catch_up_and_the_cut_off_waits(self):
        customer, config = self._client()                    # imported: nothing sent on +2 or +3
        self.assertFalse(self._blocked(customer, config, day(15)))      # used to be blocked here
        self.assertEqual(suspension_block_date(customer, config, day(15)), day(16))
        self.assertEqual(self._remind(customer, config, day(15)), 1)    # the catch-up
        self.assertFalse(self._blocked(customer, config, day(15)))
        self.assertTrue(self._blocked(customer, config, day(16)))

    def test_one_catch_up_per_client_not_one_per_invoice(self):
        customer, config = self._client(invoices=2)
        self.assertEqual(self._remind(customer, config, day(15)), 1)
        self.assertEqual(self._remind(customer, config, day(16)), 0)
        self.assertTrue(self._blocked(customer, config, day(16)))

    def test_clients_who_cannot_be_reminded_block_as_before(self):
        no_email, cfg1 = self._client(name="No Email", email="")
        no_reminders, cfg2 = self._client(name="No Reminders", email="x@example.com", reminders=False)
        self.assertTrue(self._blocked(no_email, cfg1, day(4)))
        self.assertTrue(self._blocked(no_reminders, cfg2, day(4)))

    def test_dashboard_panel_waits_for_the_catch_up(self):
        customer, config = self._client()
        rows = upcoming_blocks(day(15), horizon_days=7, customers=Customer.objects.filter(pk=customer.pk))
        self.assertEqual(rows[0]["block_date"], day(16))
