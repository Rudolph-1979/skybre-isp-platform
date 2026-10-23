"""Billing day is what decides the day. It used to be a seed applied once.

WHAT WAS WRONG

`billing_day` was read only while `next_billing_date` was empty. Once a
customer had been invoiced, the run advanced that date by adding one
period to ITSELF and never looked at billing_day again. So a customer
first invoiced on the 16th billed on the 16th for ever, and editing their
billing day changed a number nothing consulted.

The field said one thing, the form let you edit it, and the behaviour
ignored both. Two accounts here read "billing day 1" while billing on the
16th, and clearing and re-entering the field achieved nothing -- which is
exactly what you would expect of a value nothing reads.

WHAT MUST NOT BREAK WHILE FIXING IT

  * THE CATCH-UP. The date advances one period FROM THE DUE DATE, never
    from run_date. A late run that pushed the cycle forward once lost a
    whole month of billing per customer, silently. Any change here that
    skips a missed cycle reintroduces that, so there is a test for it.
  * NO DOUBLE BILL. Moving a customer's day EARLIER can put the newly
    computed date behind today, and anything on or before run_date
    invoices again on the very next run. A customer billed on the 5th and
    again on the 6th for overlapping periods is worse than one billed on
    the wrong day.

Those two pull against each other, which is the whole difficulty: one
says "a date in the past is fine, catch it up", the other says "a date in
the past bills twice". The rule that separates them is whether the
UNCLAMPED date was already behind -- if it was, this is a genuine missed
cycle; if it was not, the clamp created the problem and is backed out for
one period.
"""
import datetime
from decimal import Decimal

from django.test import TestCase

from customers.models import Customer

from . import recurring
from .models import CustomerBillingConfig, Service, Tariff


class OnBillingDayTests(TestCase):
    """The day-moving helper on its own."""

    def test_it_moves_to_the_billing_day(self):
        self.assertEqual(
            recurring._on_billing_day(datetime.date(2026, 11, 16), 1),
            datetime.date(2026, 11, 1),
        )

    def test_it_clamps_to_a_short_month(self):
        """A day of 31 in February is the 28th, not an invoice that never
        comes round."""
        self.assertEqual(
            recurring._on_billing_day(datetime.date(2027, 2, 10), 31),
            datetime.date(2027, 2, 28),
        )

    def test_it_clamps_to_a_leap_february(self):
        self.assertEqual(
            recurring._on_billing_day(datetime.date(2028, 2, 10), 31),
            datetime.date(2028, 2, 29),
        )

    def test_no_billing_day_leaves_the_date_alone(self):
        """Blank is the pre-existing behaviour and must stay harmless."""
        self.assertEqual(
            recurring._on_billing_day(datetime.date(2026, 11, 16), None),
            datetime.date(2026, 11, 16),
        )


class NextBillingDateTests(TestCase):
    """The advance itself, through a real billing run."""

    def setUp(self):
        self.tariff = Tariff.objects.create(
            name="Home 100", price=Decimal("899.00"), tax_rate_pct=Decimal("15.00"),
        )
        self.customer = Customer.objects.create(
            full_name="Rudolph Grunder", customer_id="CUS-009600",
            email="rudolph@example.com", status=Customer.Status.ACTIVE,
        )
        Service.objects.create(
            customer=self.customer, tariff=self.tariff,
            status=Service.Status.ACTIVE, start_date=datetime.date(2026, 1, 1),
        )
        self.config = CustomerBillingConfig.for_customer(self.customer)
        self.config.billing_enabled = True
        self.config.auto_create_invoices = True
        self.config.billing_day = 1
        self.config.save()

    def _advance(self, *, previous, run_date, billing_day=1):
        """Run one document generation and return the new next_billing_date."""
        self.config.billing_day = billing_day
        self.config.next_billing_date = previous
        self.config.save()
        recurring._generate_document(
            self.customer, self.config, run_date, None, True
        )
        self.config.refresh_from_db()
        return self.config.next_billing_date

    # --- the fix ----------------------------------------------------------

    def test_a_customer_on_the_wrong_day_is_moved_onto_their_billing_day(self):
        """THE ONE THIS EXISTS FOR. Invoiced on the 16th, billing day 1 --
        the next cycle is the 1st, not the 16th."""
        self.assertEqual(
            self._advance(previous=datetime.date(2026, 10, 16),
                          run_date=datetime.date(2026, 10, 16)),
            datetime.date(2026, 11, 1),
        )

    def test_a_customer_already_on_their_day_stays_there(self):
        self.assertEqual(
            self._advance(previous=datetime.date(2026, 10, 1),
                          run_date=datetime.date(2026, 10, 1)),
            datetime.date(2026, 11, 1),
        )

    def test_a_first_invoice_lands_on_the_billing_day(self):
        """next_billing_date empty: the run seeds it, and it seeds onto the
        billing day rather than onto whatever day the run happened."""
        self.assertEqual(
            self._advance(previous=None, run_date=datetime.date(2026, 10, 1)),
            datetime.date(2026, 11, 1),
        )

    def test_a_late_first_run_still_lands_on_the_billing_day(self):
        """The run slipped to the 5th; the customer belongs on the 1st."""
        self.assertEqual(
            self._advance(previous=None, run_date=datetime.date(2026, 10, 5)),
            datetime.date(2026, 11, 1),
        )

    def test_a_billing_day_of_31_survives_february(self):
        self.assertEqual(
            self._advance(previous=datetime.date(2027, 1, 31),
                          run_date=datetime.date(2027, 1, 31), billing_day=31),
            datetime.date(2027, 2, 28),
        )

    # --- what must not break ----------------------------------------------

    def test_a_run_more_than_a_period_late_still_catches_the_missed_cycle_up(self):
        """THE R575-A-CUSTOMER BUG. Due 1 October, run did not happen until
        5 December. The next date must stay 1 NOVEMBER -- in the past -- so
        the next run invoices the cycle that was missed instead of losing
        it."""
        self.assertEqual(
            self._advance(previous=datetime.date(2026, 10, 1),
                          run_date=datetime.date(2026, 12, 5)),
            datetime.date(2026, 11, 1),
        )

    def test_moving_a_day_earlier_never_lands_behind_the_run(self):
        """THE DOUBLE-BILL GUARD. Previously on the 20th, billing day now
        the 1st, run on the 5th. Clamping would give 1 November -- already
        behind the run date -- and the next run would invoice again days
        later for an overlapping period. Keep the unclamped 20 November for
        this cycle."""
        self.assertEqual(
            self._advance(previous=datetime.date(2026, 10, 20),
                          run_date=datetime.date(2026, 11, 5)),
            datetime.date(2026, 11, 20),
        )

    def test_and_then_it_corrects_itself_one_cycle_later(self):
        """No double bill, and not stuck on the wrong day either."""
        self.assertEqual(
            self._advance(previous=datetime.date(2026, 11, 20),
                          run_date=datetime.date(2026, 11, 20)),
            datetime.date(2026, 12, 1),
        )

    def test_the_billing_day_column_cannot_be_null(self):
        """The falsy-day guard in _on_billing_day is DEFENSIVE ONLY.

        An earlier version of this test asserted that a null billing day
        left the date alone, and it failed on the not-null constraint --
        the column has never allowed one. Worth pinning rather than
        deleting, because during this work two separate warnings were
        given that a blank billing day could raise inside _invoice_due
        and silently skip a customer's invoice. Neither was possible. The
        form showing the field empty was a display fault; the stored
        value was always there.
        """
        from django.db import IntegrityError, transaction

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CustomerBillingConfig.objects.filter(pk=self.config.pk).update(
                    billing_day=None
                )
