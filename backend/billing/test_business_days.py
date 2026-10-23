"""Not disconnecting people on days nobody can pay or answer the phone.

WHAT THIS IS ABOUT

A cut-off is the one thing the billing engine does that a customer cannot
undo and staff cannot see happen. Landing it on a Saturday means an
invoice they could have settled in ten minutes on a working day costs them
a weekend offline, and costs you a support call instead of a payment.

DEFERRED, NEVER FORGIVEN. Every test below checks both halves: the
suspension does not happen on the non-working day, AND it does happen on
the next working one. A rule that quietly stopped disconnecting people
would be a worse bug than the one it fixed.

THREE PLACES HAVE TO AGREE, or the platform contradicts itself in front of
a customer:

  * blocking_candidate_services -- what the run actually does;
  * suspension_block_date       -- the date the warning email PROMISES;
  * upcoming_blocks             -- the date the dashboard shows staff.

They were extracted into one predicate precisely so they could not drift,
and a date shift is exactly the kind of change that makes them drift.
"""
import datetime

from django.test import TestCase
from django.utils import timezone

from customers.models import Customer

from .business_days import is_business_day, next_business_day
from .models import CustomerBillingConfig, Invoice, PublicHoliday, Service, Tariff
from .recurring import (
    blocking_candidate_services, suspension_block_date, upcoming_blocks,
)
from decimal import Decimal

# A quiet stretch of calendar with no real South African holiday in it, so
# these tests are not silently relying on one.
MONDAY = datetime.date(2026, 11, 2)
FRIDAY = datetime.date(2026, 11, 6)
SATURDAY = datetime.date(2026, 11, 7)
SUNDAY = datetime.date(2026, 11, 8)
NEXT_MONDAY = datetime.date(2026, 11, 9)


class BusinessDayTests(TestCase):
    def test_a_weekday_is_a_business_day(self):
        self.assertTrue(is_business_day(MONDAY))
        self.assertTrue(is_business_day(FRIDAY))

    def test_a_weekend_is_not(self):
        self.assertFalse(is_business_day(SATURDAY))
        self.assertFalse(is_business_day(SUNDAY))

    def test_a_public_holiday_is_not(self):
        PublicHoliday.objects.create(date=MONDAY, name="Declared holiday")

        self.assertFalse(is_business_day(MONDAY))

    def test_a_business_day_is_returned_unchanged(self):
        """The common case, and the one a shift must not disturb."""
        self.assertEqual(next_business_day(MONDAY), MONDAY)

    def test_saturday_and_sunday_both_move_to_monday(self):
        self.assertEqual(next_business_day(SATURDAY), NEXT_MONDAY)
        self.assertEqual(next_business_day(SUNDAY), NEXT_MONDAY)

    def test_a_holiday_monday_moves_to_tuesday(self):
        PublicHoliday.objects.create(date=NEXT_MONDAY, name="Declared holiday")

        self.assertEqual(
            next_business_day(SATURDAY), NEXT_MONDAY + datetime.timedelta(days=1)
        )

    def test_a_run_of_holidays_is_walked_through(self):
        """Easter weekend: Friday and Monday both gone, so a Thursday
        cut-off deferred over it lands on the Tuesday."""
        for offset in (0, 3):
            PublicHoliday.objects.create(
                date=NEXT_MONDAY + datetime.timedelta(days=offset), name="Holiday"
            )

        self.assertEqual(
            next_business_day(SATURDAY), NEXT_MONDAY + datetime.timedelta(days=1)
        )

    def test_an_absurd_holiday_table_does_not_hang(self):
        """A bounded walk, not a while-loop. Refusing to move the date is
        visible; pushing a disconnection a fortnight out is a customer who
        is silently never cut off."""
        for offset in range(0, 40):
            PublicHoliday.objects.create(
                date=SATURDAY + datetime.timedelta(days=offset), name="Holiday"
            )

        self.assertEqual(next_business_day(SATURDAY), SATURDAY)


class SuspensionDeferralTests(TestCase):
    def setUp(self):
        self.tariff = Tariff.objects.create(
            name="Home 20", price=Decimal("500.00"), tax_rate_pct=Decimal("15.00"),
        )
        self.customer = Customer.objects.create(
            full_name="Lucille Slabbert", email="l@example.com",
            status=Customer.Status.ACTIVE, balance=Decimal("575.00"),
        )
        Service.objects.create(
            customer=self.customer, tariff=self.tariff, status=Service.Status.ACTIVE,
            start_date=MONDAY - datetime.timedelta(days=90), radius_username="lucille",
        )
        self.config = CustomerBillingConfig.for_customer(self.customer)
        self.config.billing_enabled = True
        self.config.auto_create_invoices = False
        self.config.blocking_period_days = 4
        self.config.save()
        # Due on the Wednesday, so four days later is the Sunday.
        self.due = SUNDAY - datetime.timedelta(days=4)
        invoice = Invoice.objects.create(
            customer=self.customer, status=Invoice.Status.UNPAID, date_due=self.due,
        )
        Invoice.objects.filter(pk=invoice.pk).update(total=Decimal("575.00"))

    def _candidates(self, on):
        return blocking_candidate_services(self.customer, self.config, on)

    def test_nobody_is_cut_off_on_a_sunday(self):
        self.assertEqual(self._candidates(SUNDAY), [])

    def test_nobody_is_cut_off_on_a_saturday(self):
        # The debt moves a day earlier so that SATURDAY is the natural
        # block day. Without this the test passes whether or not the rule
        # exists -- the Saturday cutoff would not have caught this invoice
        # anyway, and a test that cannot fail is not a test.
        Invoice.objects.filter(customer=self.customer).update(
            date_due=SATURDAY - datetime.timedelta(days=4)
        )

        self.assertEqual(self._candidates(SATURDAY), [])
        self.assertEqual(len(self._candidates(NEXT_MONDAY)), 1)

    def test_they_are_cut_off_on_the_monday_instead(self):
        """The other half, and the one that matters more. Deferred, not
        forgiven."""
        self.assertEqual(len(self._candidates(NEXT_MONDAY)), 1)

    def test_nobody_is_cut_off_on_a_public_holiday(self):
        PublicHoliday.objects.create(date=NEXT_MONDAY, name="Declared holiday")

        self.assertEqual(self._candidates(NEXT_MONDAY), [])
        self.assertEqual(
            len(self._candidates(NEXT_MONDAY + datetime.timedelta(days=1))), 1
        )

    def test_the_promised_date_is_the_working_day(self):
        """suspension_block_date is what the warning email puts in front of
        the customer. Telling somebody their line goes off on Sunday when it
        will not is worse than not warning them."""
        self.assertEqual(
            suspension_block_date(self.customer, self.config, MONDAY), NEXT_MONDAY
        )

    def test_the_dashboard_shows_the_same_working_day(self):
        rows = upcoming_blocks(MONDAY, horizon_days=14)

        mine = [r for r in rows if r["customer"] == self.customer.pk]
        self.assertEqual(len(mine), 1, rows)
        self.assertEqual(mine[0]["block_date"], NEXT_MONDAY)

    def test_the_dashboard_and_the_run_agree_day_by_day(self):
        """The consistency that the whole design rests on. Walked rather
        than asserted, because a shift applied in one place and not the
        other is invisible until a customer is cut off on a date nobody
        was shown."""
        rows = upcoming_blocks(MONDAY, horizon_days=14)
        promised = [r for r in rows if r["customer"] == self.customer.pk][0]["block_date"]

        day = MONDAY
        first_real = None
        while day <= MONDAY + datetime.timedelta(days=13):
            if self._candidates(day):
                first_real = day
                break
            day += datetime.timedelta(days=1)

        self.assertEqual(promised, first_real)


class HolidaySeedTests(TestCase):
    def test_easter_and_the_sunday_rule(self):
        from .management.commands.seed_public_holidays import easter_sunday, holidays_for

        self.assertEqual(easter_sunday(2026), datetime.date(2026, 4, 5))
        self.assertEqual(easter_sunday(2027), datetime.date(2027, 3, 28))

        by_date = dict(holidays_for(2026))
        # Good Friday and Family Day hang off Easter.
        self.assertIn(datetime.date(2026, 4, 3), by_date)
        self.assertIn(datetime.date(2026, 4, 6), by_date)
        # 9 August 2026 is a Sunday, so the Monday is a holiday too --
        # the part a hand-maintained list always forgets.
        self.assertIn(datetime.date(2026, 8, 10), by_date)
        self.assertIn("observed", by_date[datetime.date(2026, 8, 10)])

    def test_a_monday_that_is_already_a_holiday_is_not_duplicated(self):
        from .management.commands.seed_public_holidays import holidays_for

        for year in range(2026, 2036):
            dates = [date for date, _ in holidays_for(year)]
            self.assertEqual(len(dates), len(set(dates)), f"duplicate in {year}")

    def test_a_hand_added_holiday_survives_a_reseed(self):
        """Somebody typed it in because they knew something the command
        does not -- an election day, a company shutdown."""
        from django.core.management import call_command
        from io import StringIO

        PublicHoliday.objects.create(
            date=datetime.date(2026, 5, 20), name="Election day", seeded=False,
        )

        call_command("seed_public_holidays", "--years", "2026", "2026", "--apply",
                     stdout=StringIO())

        row = PublicHoliday.objects.get(date=datetime.date(2026, 5, 20))
        self.assertEqual(row.name, "Election day")
        self.assertFalse(row.seeded)

    def test_a_dry_run_stores_nothing(self):
        from django.core.management import call_command
        from io import StringIO

        out = StringIO()
        call_command("seed_public_holidays", "--years", "2026", "2026", stdout=out)

        self.assertEqual(PublicHoliday.objects.count(), 0)
        self.assertIn("would write", out.getvalue())
