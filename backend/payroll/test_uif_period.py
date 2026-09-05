"""UIF on a payroll run that covers more than one month.

UIF is 1% from the employee and 1% matched by the employer, on remuneration
up to a ceiling PER MONTH. The cap was being applied once per RUN, so a
two-month catch-up run charged UIF as though it were a single month: too
little deducted from the employee, and too little declared and paid over by
the employer on the EMP201.
"""
import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from payroll.models import PayrollRun, PayrollRunLine, PayrollSettings, StaffProfile
from payroll.services import (
    generate_payroll_run_lines,
    recalculate_statutory,
    uif_for_period,
)

User = get_user_model()

# 1% of the default R17,712.00 monthly ceiling -- one capped month, each side.
CAPPED_MONTH = Decimal("177.12")


def _d(text):
    return datetime.date.fromisoformat(text)


class UifForPeriodTests(TestCase):
    def setUp(self):
        self.settings_obj = PayrollSettings.load()

    def test_a_single_month_run_is_unchanged(self):
        """Almost every real run is one calendar month. Splitting the period
        by month must leave those paying exactly what they paid before."""
        self.assertEqual(
            uif_for_period(
                self.settings_obj, Decimal("30000.00"), _d("2026-07-01"), _d("2026-07-31")
            ),
            (CAPPED_MONTH, CAPPED_MONTH),
        )
    def test_a_single_month_under_the_ceiling_is_one_percent(self):
        """Most staff earn under the ceiling and never reach the cap at all."""
        self.assertEqual(
            uif_for_period(
                self.settings_obj, Decimal("10000.00"), _d("2026-07-01"), _d("2026-07-31")
            ),
            (Decimal("100.00"), Decimal("100.00")),
        )
    def test_two_months_over_the_ceiling_are_capped_twice(self):
        """1 July to 31 August pays two months' salary, so it owes two
        months' UIF. The old code capped the whole run at one month's
        ceiling and took R177.12 where R354.24 was due."""
        self.assertEqual(
            uif_for_period(
                self.settings_obj, Decimal("60000.00"), _d("2026-07-01"), _d("2026-08-31")
            ),
            (CAPPED_MONTH * 2, CAPPED_MONTH * 2),
        )
    def test_two_months_under_the_ceiling_stay_one_percent_of_the_whole(self):
        """Someone on R10,000 a month is under the ceiling in both months,
        so splitting the period must not change what they pay: still 1% of
        everything earned."""
        self.assertEqual(
            uif_for_period(
                self.settings_obj, Decimal("20000.00"), _d("2026-07-01"), _d("2026-08-31")
            ),
            (Decimal("200.00"), Decimal("200.00")),
        )
    def test_a_year_long_run_is_twelve_capped_months(self):
        """The extreme version of the same mistake -- a whole year of
        back-pay in one run would have declared one month of UIF."""
        self.assertEqual(
            uif_for_period(
                self.settings_obj, Decimal("360000.00"), _d("2026-01-01"), _d("2026-12-31")
            ),
            (CAPPED_MONTH * 12, CAPPED_MONTH * 12),
        )
    def test_a_cycle_that_straddles_month_end_is_still_ONE_cycle(self):
        """The 26th-to-25th pay cycle, which is how a great many South
        African employers run payroll. It touches two calendar months and is
        one month's pay, so it gets ONE ceiling.

        This is a regression test with a story: an earlier version of this
        function counted calendar months instead of pay cycles and charged
        such a cycle two ceilings -- R256.62 instead of R177.12, every
        month, off everyone earning over the ceiling, with the employer
        over-declaring the same amount on the EMP201. The test that used to
        sit here asserted the two-ceiling answer, so a green suite defended
        the bug.
        """
        self.assertEqual(
            uif_for_period(
                self.settings_obj, Decimal("41075.27"), _d("2026-08-26"), _d("2026-09-25")
            ),
            (CAPPED_MONTH, CAPPED_MONTH),
        )

    def test_a_mid_month_to_mid_month_cycle_is_also_one_cycle(self):
        """15 July to 14 August is the same shape: one month of pay."""
        self.assertEqual(
            uif_for_period(
                self.settings_obj, Decimal("60000.00"), _d("2026-07-15"), _d("2026-08-14")
            ),
            (CAPPED_MONTH, CAPPED_MONTH),
        )

    def test_two_full_months_is_two_cycles(self):
        """The case this function was written for still works: a catch-up
        run covering two whole monthly cycles gets two ceilings."""
        self.assertEqual(
            uif_for_period(
                self.settings_obj, Decimal("60000.00"), _d("2026-07-01"), _d("2026-08-31")
            ),
            (CAPPED_MONTH * 2, CAPPED_MONTH * 2),
        )
    def test_a_period_crossing_a_year_boundary_caps_both_months(self):
        """December/January is exactly when a catch-up run gets done, and
        the month walk has to roll the year over."""
        self.assertEqual(
            uif_for_period(
                self.settings_obj, Decimal("60000.00"), _d("2026-12-01"), _d("2027-01-31")
            ),
            (CAPPED_MONTH * 2, CAPPED_MONTH * 2),
        )
    def test_nothing_earned_costs_nothing(self):
        """An unpaid line must not have UIF taken off it, however many
        months the run covers."""
        self.assertEqual(
            uif_for_period(
                self.settings_obj, Decimal("0.00"), _d("2026-07-01"), _d("2026-08-31")
            ),
            (Decimal("0.00"), Decimal("0.00")),
        )


class MultiMonthRunUifTests(TestCase):
    def setUp(self):
        PayrollSettings.load()
        self.staff = User.objects.create_user(username="thabo", password="x", role="admin")
        StaffProfile.objects.create(
            user=self.staff,
            pay_type=StaffProfile.PayType.SALARY,
            monthly_salary=Decimal("30000.00"),
        )

    def test_a_two_month_run_deducts_two_months_of_uif(self):
        """The situation that started this: a catch-up run covering July and
        August pays two months' salary on the payslip, so it must take two
        months' UIF off it and declare two months from the employer."""
        run = PayrollRun.objects.create(
            period_start=_d("2026-07-01"), period_end=_d("2026-08-31")
        )
        generate_payroll_run_lines(run)
        line = run.lines.get(staff=self.staff)
        self.assertEqual(line.gross_pay, Decimal("60000.00"))
        self.assertEqual(line.uif_employee, CAPPED_MONTH * 2)
        self.assertEqual(line.uif_employer, CAPPED_MONTH * 2)
    def test_editing_a_bonus_on_a_two_month_run_keeps_both_months_capped(self):
        """Typing a bonus into a line recalculates UIF down a different code
        path from generation. That path used to put the single-month cap
        back, so a correct run went wrong the moment anyone edited it."""
        run = PayrollRun.objects.create(
            period_start=_d("2026-07-01"), period_end=_d("2026-08-31")
        )
        line = PayrollRunLine.objects.create(
            payroll_run=run,
            staff=self.staff,
            pay_type=StaffProfile.PayType.SALARY,
            gross_pay=Decimal("60000.00"),
            additional_amount=Decimal("5000.00"),
        )
        recalculate_statutory(line)
        line.refresh_from_db()
        self.assertEqual(line.uif_employee, CAPPED_MONTH * 2)
        self.assertEqual(line.uif_employer, CAPPED_MONTH * 2)
