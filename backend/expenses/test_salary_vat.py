"""No input VAT can be claimed on wages.

Expense.vat_rate_pct defaults to 15 for every category, Salaries/Payroll
included. Capturing a payroll run on the Expenses tab and leaving that
field alone therefore claimed 15% input VAT on the entire wage bill --
the largest single cost this business has -- and understated the VAT
payable on the return by that amount. There is no tax invoice behind a
salary and no input tax to claim.

The bank-feed side of expense capture already refused to guess a rate,
and its own code comments name this exact case ("a salary run ... could
be click-through confirmed as carrying Input VAT that was never charged
-- SARS penalises overclaimed Input VAT"). The manual side did not.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from expenses.models import Expense

User = get_user_model()


class SalaryExpenseVatTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.staff = User.objects.create_user(
            username="bookkeeper", password="pw-for-tests", role=User.Role.ACCOUNTS
        )
        self.client.force_authenticate(self.staff)

    def _post(self, **overrides):
        body = {
            "category": Expense.Category.SALARIES,
            "description": "September payroll",
            "date": "2026-09-25",
            "amount_excl_vat": "184000.00",
            "vat_rate_pct": "15",
            "supplier_name": "Staff",
        }
        body.update(overrides)
        return self.client.post("/api/expenses/", body, format="json")

    def test_a_salary_expense_at_15_percent_is_refused(self):
        response = self._post()
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("vat_rate_pct", response.data)
        self.assertFalse(Expense.objects.exists())

    def test_the_message_says_what_to_do_instead(self):
        response = self._post()
        message = " ".join(str(m) for m in response.data["vat_rate_pct"]).lower()
        self.assertIn("no vat", message)
        self.assertIn("professional fees", message)

    def test_a_salary_expense_at_zero_is_accepted(self):
        response = self._post(vat_rate_pct="0")
        self.assertEqual(response.status_code, 201, response.data)
        expense = Expense.objects.get()
        self.assertEqual(expense.vat_amount, Decimal("0.00"))

    def test_editing_an_existing_salary_expense_up_to_15_is_refused(self):
        """The rule has to hold on the edit path too -- an expense captured
        correctly at 0 must not be quietly bumped to 15 afterwards."""
        self._post(vat_rate_pct="0")
        expense = Expense.objects.get()
        response = self.client.patch(
            f"/api/expenses/{expense.pk}/", {"vat_rate_pct": "15"}, format="json"
        )
        self.assertEqual(response.status_code, 400, response.data)
        expense.refresh_from_db()
        self.assertEqual(expense.vat_rate_pct, Decimal("0.00"))

    def test_recategorising_an_ordinary_expense_to_salaries_is_refused(self):
        """The other way into the same wrong state: a 15% expense whose
        category is changed to Salaries afterwards."""
        response = self._post(category=Expense.Category.BANDWIDTH, vat_rate_pct="15")
        self.assertEqual(response.status_code, 201, response.data)
        expense = Expense.objects.get()
        response = self.client.patch(
            f"/api/expenses/{expense.pk}/",
            {"category": Expense.Category.SALARIES},
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.data)

    # ---- everything else still carries VAT normally ---------------------

    def test_a_bandwidth_expense_still_defaults_to_15(self):
        response = self._post(category=Expense.Category.BANDWIDTH, description="IP transit")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Expense.objects.get().vat_rate_pct, Decimal("15.00"))

    def test_a_payroll_bureau_fee_can_still_be_captured_with_vat(self):
        """The message points here, so it has to work: a payroll bureau's
        own invoice is a professional fee and does carry VAT."""
        response = self._post(
            category=Expense.Category.PROFESSIONAL,
            description="Payroll bureau fee", amount_excl_vat="1500.00", vat_rate_pct="15",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Expense.objects.get().vat_amount, Decimal("225.00"))

    # ---- the two holes the first version of this guard had ---------------

    def test_omitting_the_vat_rate_entirely_is_still_refused(self):
        """The likeliest way to book a payroll run: fill the form in and
        never touch the VAT field. vat_rate_pct has a MODEL DEFAULT of 15,
        so an absent key is not "no rate" -- it is 15.

        The first version of this guard read the incoming data only, so it
        saw no rate, stayed quiet, and let the row save at 15%: R27,600 of
        input VAT claimed on a R184,000 payroll. Every test in this file
        sent an explicit rate, so the suite was green and the hole was
        exactly the case the guard existed for.
        """
        body = {
            "category": Expense.Category.SALARIES,
            "description": "September payroll",
            "date": "2026-09-25",
            "amount_excl_vat": "184000.00",
            "supplier_name": "Staff",
        }
        response = self.client.post("/api/expenses/", body, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("vat_rate_pct", response.data)
        self.assertFalse(Expense.objects.exists())

    def test_an_unrelated_edit_to_an_old_salaries_expense_is_allowed(self):
        """Rows captured at 15% before this rule existed are still in the
        database. Enforcing the rule on every save meant they could not be
        edited at all -- correcting a description came back 400 about a VAT
        rate the request never mentioned, so the row could not be tidied up
        or even re-described."""
        legacy = Expense.objects.create(
            category=Expense.Category.SALARIES, description="Aug payroll",
            date="2026-08-25", amount_excl_vat=Decimal("184000.00"),
            vat_rate_pct=Decimal("15.00"),
        )
        response = self.client.patch(
            f"/api/expenses/{legacy.pk}/", {"description": "Aug payroll (corrected)"}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)
        legacy.refresh_from_db()
        self.assertEqual(legacy.description, "Aug payroll (corrected)")

    def test_but_setting_that_old_row_to_salaries_again_still_checks_it(self):
        """The escape hatch must not become a way round the rule: a request
        that names the category or the rate is judged on what it writes."""
        legacy = Expense.objects.create(
            category=Expense.Category.BANDWIDTH, description="IP transit",
            date="2026-08-25", amount_excl_vat=Decimal("1000.00"),
            vat_rate_pct=Decimal("15.00"),
        )
        response = self.client.patch(
            f"/api/expenses/{legacy.pk}/",
            {"category": Expense.Category.SALARIES}, format="json",
        )
        self.assertEqual(response.status_code, 400, response.data)

