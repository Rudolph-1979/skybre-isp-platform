"""The chart of accounts, and what must not move once money is filed.

The dangerous thing about a default is that it keeps applying. A
supplier's category pre-fills the account on an expense -- useful. If
that pre-fill were re-derived every time the expense was saved, then
re-categorising a supplier next March would silently re-post every
invoice ever captured from them, including ones inside VAT periods
already filed with SARS. The account is stamped ONCE, on the way in, and
these tests are mostly about that word.

The rest is about an account never quietly taking records with it:
deleting one that expenses are filed against would either delete them or
detach them, and a detached expense is a hole in a period that nothing on
screen would report.
"""
import datetime
from decimal import Decimal
from importlib import import_module

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.testing import ALL_SECTIONS
from expenses.models import Expense
from inventory.models import Supplier, SupplierCategory
from ledger.models import Account, by_key, expense_key

User = get_user_model()


def seeded(key):
    return Account.objects.get(system_key=key)


class SeedTests(TestCase):
    """The seed runs as a migration, so by the time any test starts it has
    already happened. These assert what it left behind."""

    def test_every_expense_category_has_an_account(self):
        """A category with no account is a gap that only shows up the
        first time somebody books that kind of cost."""
        for value, _label in Expense.Category.choices:
            with self.subTest(category=value):
                self.assertIsNotNone(by_key(expense_key(value)))

    def test_the_chart_is_not_only_expenses(self):
        """Seeded from what the platform tracks, not from the expense list
        alone -- an expenses-only chart has no other side and could never
        balance."""
        types = set(Account.objects.values_list("type", flat=True))

        self.assertIn("asset", types)
        self.assertIn("liability", types)
        self.assertIn("income", types)
        self.assertIn("equity", types)

    def test_the_vat_account_is_found_by_meaning_not_by_number(self):
        """Somebody will renumber it to match their accountant's chart.
        Code that needs it must still find it."""
        vat = seeded("vat_control")
        vat.code = "9999/1"
        vat.name = "VAT (SARS)"
        vat.save()

        self.assertEqual(by_key("vat_control"), vat)


class StampingTests(TestCase):
    """The account is taken from the supplier's category once, at capture."""

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(
            User.objects.create_user(
                username="books", password="x", role="accounts", is_staff=True,
                allowed_sections=ALL_SECTIONS,
            )
        )
        self.bandwidth = seeded(expense_key("bandwidth"))
        self.rent = seeded(expense_key("rent"))
        self.transit = SupplierCategory.objects.create(
            name="Transit", default_account=self.bandwidth
        )
        self.supplier = Supplier.objects.create(name="Teraco", category=self.transit)

    def _post(self, **overrides):
        body = {
            "supplier": self.supplier.pk,
            "category": Expense.Category.BANDWIDTH,
            "description": "September transit",
            "date": "2026-09-01",
            "amount_excl_vat": "12000.00",
            "vat_rate_pct": "15",
        }
        body.update(overrides)
        return self.client.post("/api/expenses/", body, format="json")

    def test_capturing_an_expense_takes_the_category_s_account(self):
        response = self._post()

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Expense.objects.get().account, self.bandwidth)

    def test_an_account_named_on_the_request_wins(self):
        """The pre-fill is a convenience, not a rule -- one invoice from a
        bandwidth supplier really can be something else."""
        response = self._post(account=self.rent.pk)

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Expense.objects.get().account, self.rent)

    def test_a_supplier_with_no_category_leaves_the_account_empty(self):
        """Empty, not guessed. An account picked from the expense category
        would look identical on screen and be nobody's decision."""
        plain = Supplier.objects.create(name="Corner shop")

        response = self._post(supplier=plain.pk)

        self.assertEqual(response.status_code, 201, response.data)
        self.assertIsNone(Expense.objects.get().account)

    def test_re_categorising_a_supplier_does_not_move_old_expenses(self):
        """The whole point. That expense may sit inside a VAT period
        already filed with SARS; moving it would change a declared figure
        months later, from a screen that says nothing about VAT."""
        self._post()
        expense = Expense.objects.get()

        self.transit.default_account = self.rent
        self.transit.save(update_fields=["default_account"])
        expense.description = "September transit (corrected)"
        expense.save()

        expense.refresh_from_db()
        self.assertEqual(expense.account, self.bandwidth)

    def test_editing_an_expense_does_not_re_derive_its_account(self):
        """Same rule through the API, which is how it will actually
        happen -- somebody fixes a typo in the description."""
        self._post()
        expense = Expense.objects.get()
        self.transit.default_account = self.rent
        self.transit.save(update_fields=["default_account"])

        response = self.client.patch(
            f"/api/expenses/{expense.pk}/", {"description": "Fixed"}, format="json"
        )

        self.assertEqual(response.status_code, 200, response.data)
        expense.refresh_from_db()
        self.assertEqual(expense.account, self.bandwidth)

    def test_an_expense_that_lost_its_account_can_be_given_one(self):
        """Deactivating an account SET_NULLs nothing, but deleting a
        non-system one does -- and that expense has to be fixable."""
        self._post()
        expense = Expense.objects.get()
        Expense.objects.filter(pk=expense.pk).update(account=None)

        response = self.client.patch(
            f"/api/expenses/{expense.pk}/", {"account": self.rent.pk}, format="json"
        )

        self.assertEqual(response.status_code, 200, response.data)
        expense.refresh_from_db()
        self.assertEqual(expense.account, self.rent)


class BackfillTests(TestCase):
    """The migration that files every pre-existing expense onto the chart."""

    def _backfill(self):
        module = import_module("expenses.migrations.0003_expense_account")
        module.map_categories(django_apps, None)

    def _expense(self, category, account=None):
        return Expense.objects.create(
            description="Historic", date=datetime.date(2026, 3, 1),
            amount_excl_vat=Decimal("100.00"), vat_rate_pct=Decimal("15"),
            category=category, account=account,
        )

    def test_every_category_maps_to_its_account(self):
        for value, _label in Expense.Category.choices:
            self._expense(value)

        Expense.objects.update(account=None)
        self._backfill()

        for expense in Expense.objects.all():
            with self.subTest(category=expense.category):
                self.assertEqual(expense.account, seeded(expense_key(expense.category)))

    def test_it_does_not_overwrite_an_account_somebody_chose(self):
        """Re-running must not undo a correction. Only rows with no
        account at all are touched."""
        rent = seeded(expense_key("rent"))
        expense = self._expense(Expense.Category.BANDWIDTH, account=rent)

        self._backfill()

        expense.refresh_from_db()
        self.assertEqual(expense.account, rent)


class AccountLifecycleTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(
            User.objects.create_user(
                username="books", password="x", role="accounts", is_staff=True,
                allowed_sections=ALL_SECTIONS,
            )
        )

    def test_a_system_account_cannot_be_deleted(self):
        vat = seeded("vat_control")

        response = self.client.delete(f"/api/chart-accounts/{vat.pk}/")

        self.assertEqual(response.status_code, 400)
        self.assertTrue(Account.objects.filter(pk=vat.pk).exists())

    def test_a_system_account_can_be_renamed_and_renumbered(self):
        """Matching an accountant's existing chart has to be possible --
        that is the whole reason system_key exists."""
        vat = seeded("vat_control")

        response = self.client.patch(
            f"/api/chart-accounts/{vat.pk}/", {"code": "2200", "name": "VAT payable"}, format="json"
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(by_key("vat_control").code, "2200")

    def test_an_account_with_expenses_on_it_cannot_be_deleted(self):
        account = Account.objects.create(code="6950", name="Sundries", type="expense")
        Expense.objects.create(
            description="Something", date=datetime.date(2026, 3, 1),
            amount_excl_vat=Decimal("10.00"), vat_rate_pct=Decimal("15"), account=account,
        )

        response = self.client.delete(f"/api/chart-accounts/{account.pk}/")

        self.assertEqual(response.status_code, 400)
        self.assertIn("1 expense", response.data["detail"])

    def test_an_unused_account_somebody_added_can_be_deleted(self):
        account = Account.objects.create(code="6960", name="Typo", type="expense")

        response = self.client.delete(f"/api/chart-accounts/{account.pk}/")

        self.assertEqual(response.status_code, 204)

    def test_deactivating_keeps_it_on_the_expenses_already_filed(self):
        """The alternative to deleting. The account stays where it is on
        last year's figures and stops appearing in pickers."""
        account = Account.objects.create(code="6970", name="Retired", type="expense")
        expense = Expense.objects.create(
            description="Old", date=datetime.date(2026, 1, 1),
            amount_excl_vat=Decimal("10.00"), vat_rate_pct=Decimal("15"), account=account,
        )

        self.client.patch(f"/api/chart-accounts/{account.pk}/", {"is_active": False}, format="json")

        expense.refresh_from_db()
        self.assertEqual(expense.account, account)

    def test_the_chart_is_gated_on_the_accountant_section(self):
        self.client.force_authenticate(
            User.objects.create_user(
                username="tech", password="x", role="technician", is_staff=True,
                allowed_sections=["vehicles"],
            )
        )

        self.assertEqual(self.client.get("/api/chart-accounts/").status_code, 403)


class CategoryDeletionTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(
            User.objects.create_user(
                username="boss", password="x", role="admin", is_staff=True,
            )
        )
        self.category = SupplierCategory.objects.create(
            name="Transit", default_account=seeded(expense_key("bandwidth"))
        )

    def test_deleting_a_category_with_suppliers_asks_first(self):
        """"Delete Transit" reads like removing a label and actually
        un-categorises every supplier under it."""
        Supplier.objects.create(name="Teraco", category=self.category)

        response = self.client.delete(f"/api/supplier-categories/{self.category.pk}/")

        self.assertEqual(response.status_code, 400)
        self.assertIn("1 supplier", response.data["detail"])

    def test_confirming_goes_ahead_and_leaves_the_suppliers_standing(self):
        supplier = Supplier.objects.create(name="Teraco", category=self.category)

        response = self.client.delete(
            f"/api/supplier-categories/{self.category.pk}/?confirm=yes"
        )

        self.assertEqual(response.status_code, 204)
        supplier.refresh_from_db()
        self.assertIsNone(supplier.category)

    def test_an_empty_category_goes_without_asking(self):
        response = self.client.delete(f"/api/supplier-categories/{self.category.pk}/")

        self.assertEqual(response.status_code, 204)
