"""Which day an anniversary-billed customer is actually due.

Two faults sat in the same pair of functions, and both mean an invoice
that should have been raised was not.

created_at is stored in UTC and Skybre is UTC+2, so a customer signed up
at 00:30 on the 1st is stored as 22:30 on the last day of the PREVIOUS
month. Reading .day off the raw column gave 31 rather than 1.

And the due test was an exact match on day-of-month, so an anniversary of
the 29th, 30th or 31st never came round in a month without that day: no
invoice at all in April, June, September or November, and never in
February, for as long as they stayed a customer.
"""
import datetime

from django.test import TestCase
from django.utils import timezone

from billing.models import CustomerBillingConfig
from billing.recurring import _billing_due_day, _invoice_due
from customers.models import Customer


class AnniversaryBillingDayTests(TestCase):
    def _customer(self, local_dt):
        customer = Customer.objects.create(
            full_name="Anniversary Annie", email="annie@example.com"
        )
        Customer.objects.filter(pk=customer.pk).update(created_at=local_dt)
        customer.refresh_from_db()
        config = CustomerBillingConfig.for_customer(customer)
        config.use_date_of_customer_creation = True
        config.auto_create_invoices = True
        config.next_billing_date = None
        config.save()
        return customer, config

    def _at(self, year, month, day, hour, minute=0):
        return datetime.datetime(
            year, month, day, hour, minute, tzinfo=timezone.get_current_timezone()
        )

    def test_a_customer_signed_up_just_after_midnight_bills_on_that_day(self):
        """00:30 on 1 February SAST is 22:30 on 31 January UTC. Their
        billing day is the 1st, which is the day they actually signed up."""
        customer, config = self._customer(self._at(2026, 2, 1, 0, 30))
        self.assertEqual(_billing_due_day(customer, config), 1)

    def test_the_stored_utc_day_is_not_what_gets_used(self):
        """Guarding the mechanism, not just the result: the raw column
        really does say 31 here, so a regression would be invisible if the
        test only checked a mid-afternoon signup."""
        customer, _ = self._customer(self._at(2026, 2, 1, 0, 30))
        self.assertEqual(customer.created_at.day, 31, "the stored UTC day")
        self.assertEqual(timezone.localtime(customer.created_at).day, 1)

    def test_an_anniversary_on_the_31st_still_bills_in_a_30_day_month(self):
        """April has no 31st. The customer is due on its last day, not
        never -- which is what an exact day match gave them."""
        customer, config = self._customer(self._at(2026, 1, 31, 12))
        due = [d for d in range(1, 31) if _invoice_due(customer, config, datetime.date(2026, 4, d))]
        self.assertEqual(due, [30], "expected exactly one due day in April, the 30th")

    def test_and_in_february(self):
        customer, config = self._customer(self._at(2026, 1, 31, 12))
        due = [d for d in range(1, 29) if _invoice_due(customer, config, datetime.date(2026, 2, d))]
        self.assertEqual(due, [28])

    def test_an_ordinary_anniversary_is_unchanged(self):
        """The common case must not move: the 12th is the 12th."""
        customer, config = self._customer(self._at(2026, 1, 12, 9))
        self.assertEqual(_billing_due_day(customer, config), 12)
        due = [d for d in range(1, 31) if _invoice_due(customer, config, datetime.date(2026, 4, d))]
        self.assertEqual(due, [12])
