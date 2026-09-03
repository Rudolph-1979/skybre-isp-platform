"""Salaried pay for a run's period, including periods that aren't one month.

The old computation compared the period's length against the days in its
FIRST month and paid a flat month's salary whenever the period was at
least that long:

    month_length = days_in_month(run.period_start)
    period_days = (period_end - period_start).days + 1
    if period_days >= month_length: base_pay = salary

Nothing constrains a run to a single calendar month -- the only
validation is period_end >= period_start -- so a two-month catch-up run
paid one month's salary for two months worked. Every salaried employee
underpaid by exactly one month, on a payslip PDF and a bank-export CSV
that both printed it without complaint.
"""
import datetime
from decimal import Decimal

from django.test import TestCase

from payroll.services import salary_for_period

SALARY = Decimal("30000.00")


def _d(text):
    return datetime.date.fromisoformat(text)


class SalaryForPeriodTests(TestCase):
    # ---- the ordinary case, which must not change ----------------------

    def test_a_full_31_day_month_pays_one_salary(self):
        self.assertEqual(salary_for_period(SALARY, _d("2026-07-01"), _d("2026-07-31")), SALARY)

    def test_a_full_30_day_month_pays_one_salary(self):
        self.assertEqual(salary_for_period(SALARY, _d("2026-09-01"), _d("2026-09-30")), SALARY)

    def test_a_full_february_pays_one_salary(self):
        self.assertEqual(salary_for_period(SALARY, _d("2026-02-01"), _d("2026-02-28")), SALARY)

    def test_a_full_leap_february_pays_one_salary(self):
        self.assertEqual(salary_for_period(SALARY, _d("2028-02-01"), _d("2028-02-29")), SALARY)

    # ---- the bug -------------------------------------------------------

    def test_two_full_months_pay_two_salaries(self):
        """1 July to 31 August is 62 days against July's 31, so the old
        code paid R30,000 for two months worked."""
        self.assertEqual(
            salary_for_period(SALARY, _d("2026-07-01"), _d("2026-08-31")), SALARY * 2
        )

    def test_three_full_months_pay_three_salaries(self):
        self.assertEqual(
            salary_for_period(SALARY, _d("2026-07-01"), _d("2026-09-30")), SALARY * 3
        )

    def test_a_year_pays_twelve_salaries(self):
        self.assertEqual(
            salary_for_period(SALARY, _d("2026-01-01"), _d("2026-12-31")), SALARY * 12
        )

    def test_a_period_spanning_two_part_months_adds_both_halves(self):
        """15 July to 14 August: 17 days of July's 31, 14 of August's 31."""
        expected = (
            (SALARY * Decimal(17) / Decimal(31)).quantize(Decimal("0.01"))
            + (SALARY * Decimal(14) / Decimal(31)).quantize(Decimal("0.01"))
        )
        self.assertEqual(salary_for_period(SALARY, _d("2026-07-15"), _d("2026-08-14")), expected)

    def test_a_full_month_plus_a_few_days_pays_more_than_one_salary(self):
        """The direction that matters: it must never come out at exactly
        one salary when more than a month was worked."""
        result = salary_for_period(SALARY, _d("2026-07-01"), _d("2026-08-05"))
        self.assertGreater(result, SALARY)
        self.assertLess(result, SALARY * 2)

    # ---- part months ---------------------------------------------------

    def test_a_single_day_is_prorated(self):
        self.assertEqual(
            salary_for_period(SALARY, _d("2026-07-01"), _d("2026-07-01")),
            (SALARY * Decimal(1) / Decimal(31)).quantize(Decimal("0.01")),
        )

    def test_half_a_month_is_prorated(self):
        self.assertEqual(
            salary_for_period(SALARY, _d("2026-07-01"), _d("2026-07-15")),
            (SALARY * Decimal(15) / Decimal(31)).quantize(Decimal("0.01")),
        )

    def test_a_mid_month_start_to_month_end_is_prorated(self):
        self.assertEqual(
            salary_for_period(SALARY, _d("2026-07-20"), _d("2026-07-31")),
            (SALARY * Decimal(12) / Decimal(31)).quantize(Decimal("0.01")),
        )

    # ---- edges ---------------------------------------------------------

    def test_a_zero_salary_pays_nothing(self):
        self.assertEqual(
            salary_for_period(Decimal("0"), _d("2026-07-01"), _d("2026-08-31")), Decimal("0.00")
        )

    def test_a_period_crossing_a_year_boundary_adds_up(self):
        self.assertEqual(
            salary_for_period(SALARY, _d("2026-12-01"), _d("2027-01-31")), SALARY * 2
        )

    def test_a_period_crossing_february_adds_up(self):
        self.assertEqual(
            salary_for_period(SALARY, _d("2026-01-01"), _d("2026-03-31")), SALARY * 3
        )
