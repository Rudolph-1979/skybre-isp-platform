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
