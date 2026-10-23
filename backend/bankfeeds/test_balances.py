"""What a bank account's balance is, and what it must never quietly be.

The figure on this page is the one somebody holds next to internet
banking. Three things about it are load-bearing:

IT IS BUILT FROM THE BANK'S OWN LINES, not from payments and expenses.
Those are different sets: a card payment recorded against a customer, a
transfer between two of these accounts, a bank charge nobody has captured
yet. A balance summed from payments and expenses would miss some and
double others, and would be wrong in a way that looks plausible.

AN "IGNORED" TRANSACTION STILL HAPPENED. Ignoring a line means "this is
not a customer payment I need to match", not "this did not leave the
account". Excluding them would drift the balance by the total of
everything anybody has ever dismissed.

A MISMATCH IS THE FINDING, NOT A BUG. When the calculated balance differs
from what the bank says, that gap means a statement is missing, imported
twice, or the opening balance is dated wrong. Nothing here adjusts
anything to make the two agree -- doing that would erase the evidence and
leave the missing transactions missing.
"""
import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from bankfeeds.models import BankAccount, BankTransaction

User = get_user_model()

OPENED = datetime.date(2026, 5, 1)


class BalanceArithmeticTests(TestCase):
    def setUp(self):
        self.account = BankAccount.objects.create(
            name="FNB Cheque", opening_balance=Decimal("1000.00"), opening_balance_date=OPENED,
        )

    def _txn(self, day, amount, status=BankTransaction.Status.UNMATCHED, ref=None):
        return BankTransaction.objects.create(
            account=self.account, source=BankTransaction.Source.CSV_IMPORT,
            external_id=ref or f"{day}-{amount}", date=day, amount=Decimal(amount), status=status,
        )

    def test_the_balance_is_the_opening_plus_everything_since(self):
        self._txn(datetime.date(2026, 5, 3), "500.00")
        self._txn(datetime.date(2026, 5, 9), "-120.50")

        self.assertEqual(self.account.current_balance, Decimal("1379.50"))

    def test_an_account_with_no_opening_date_has_no_balance(self):
        """Not zero. An opening balance that cannot be placed in time is
        unusable, and R0.00 would be a confident claim about an account
        that might hold anything."""
        self.account.opening_balance_date = None
        self.account.save(update_fields=["opening_balance_date"])

        self.assertIsNone(self.account.current_balance)

    def test_transactions_before_the_opening_date_are_not_counted_twice(self):
        """The opening balance already includes them -- that is what an
        opening balance is. Counting them again would double a whole
        statement's worth of history the first time one is imported."""
        self._txn(datetime.date(2026, 4, 28), "9999.00")
        self._txn(datetime.date(2026, 5, 3), "500.00")

        self.assertEqual(self.account.current_balance, Decimal("1500.00"))

    def test_a_transaction_on_the_opening_date_itself_counts(self):
        """The opening balance is the balance BEFORE the first transaction
        on the feed, so the boundary is inclusive. Getting this the other
        way round loses a day of transactions, silently, exactly once per
        account -- which is the hardest kind of error to find later."""
        self._txn(OPENED, "250.00")

        self.assertEqual(self.account.current_balance, Decimal("1250.00"))

    def test_ignored_transactions_still_move_the_balance(self):
        """A bank charge marked "ignored" still left the account."""
        self._txn(datetime.date(2026, 5, 3), "-75.00", status=BankTransaction.Status.IGNORED)

        self.assertEqual(self.account.current_balance, Decimal("925.00"))

    def test_confirmed_transactions_are_counted_once_not_twice(self):
        """Confirming a transaction creates a Payment or an Expense. The
        balance must follow the bank line, not both -- otherwise every
        matched payment counts double."""
        self._txn(datetime.date(2026, 5, 3), "500.00", status=BankTransaction.Status.CONFIRMED)

        self.assertEqual(self.account.current_balance, Decimal("1500.00"))

    def test_the_balance_can_be_asked_for_as_at_a_date(self):
        self._txn(datetime.date(2026, 5, 3), "500.00")
        self._txn(datetime.date(2026, 6, 15), "700.00")

        self.assertEqual(
            self.account.balance_as_at(datetime.date(2026, 5, 31)), Decimal("1500.00")
        )

    def test_another_account_s_transactions_stay_out_of_it(self):
        other = BankAccount.objects.create(
            name="FNB Savings", opening_balance=Decimal("0"), opening_balance_date=OPENED
        )
        BankTransaction.objects.create(
            account=other, source=BankTransaction.Source.CSV_IMPORT,
            external_id="x", date=datetime.date(2026, 5, 3), amount=Decimal("5000.00"),
        )

        self.assertEqual(self.account.current_balance, Decimal("1000.00"))

    def test_an_account_with_no_transactions_is_its_opening_balance(self):
        self.assertEqual(self.account.current_balance, Decimal("1000.00"))


class VarianceTests(TestCase):
    def setUp(self):
        self.account = BankAccount.objects.create(
            name="FNB Cheque", opening_balance=Decimal("1000.00"), opening_balance_date=OPENED,
        )

    def test_no_variance_until_somebody_has_checked(self):
        self.assertIsNone(self.account.balance_variance)

    def test_agreeing_shows_as_zero_not_as_nothing(self):
        """Zero and "never checked" are different facts, and only one of
        them is reassuring."""
        self.account.confirmed_balance = Decimal("1000.00")
        self.account.save(update_fields=["confirmed_balance"])

        self.assertEqual(self.account.balance_variance, Decimal("0.00"))

    def test_a_missing_statement_shows_up_as_the_amount_missing(self):
        self.account.confirmed_balance = Decimal("1250.00")
        self.account.save(update_fields=["confirmed_balance"])

        self.assertEqual(self.account.balance_variance, Decimal("-250.00"))


class ConfirmBalanceEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(
            username="boss", password="x", role="admin", is_staff=True
        )
        self.client.force_authenticate(self.admin)
        self.account = BankAccount.objects.create(
            name="FNB Cheque", opening_balance=Decimal("1000.00"), opening_balance_date=OPENED,
        )

    def _confirm(self, value):
        return self.client.post(
            f"/api/bank-accounts/{self.account.pk}/confirm-balance/",
            {"confirmed_balance": value}, format="json",
        )

    def test_recording_a_balance_stamps_who_and_when(self):
        response = self._confirm("950.00")

        self.assertEqual(response.status_code, 200, response.data)
        self.account.refresh_from_db()
        self.assertEqual(self.account.confirmed_balance, Decimal("950.00"))
        self.assertEqual(self.account.confirmed_balance_by, self.admin)
        self.assertIsNotNone(self.account.confirmed_balance_at)

    def test_it_does_not_adjust_anything_to_make_the_two_agree(self):
        """The instinct on seeing a mismatch is to nudge the opening
        balance until it disappears. That hides the missing transactions
        rather than finding them, so the gap is left standing."""
        self._confirm("950.00")

        self.account.refresh_from_db()
        self.assertEqual(self.account.opening_balance, Decimal("1000.00"))
        self.assertEqual(self.account.current_balance, Decimal("1000.00"))
        self.assertEqual(self.account.balance_variance, Decimal("50.00"))

    def test_rubbish_is_a_message_not_a_500(self):
        self.assertEqual(self._confirm("about a grand").status_code, 400)

    def test_an_empty_value_is_refused(self):
        self.assertEqual(self._confirm("").status_code, 400)

    def test_a_negative_balance_is_allowed(self):
        """An overdrawn cheque account is an ordinary Tuesday."""
        self.assertEqual(self._confirm("-4300.25").status_code, 200)

    def test_recording_a_balance_is_not_admin_only(self):
        """Checking a balance is the job of whoever does the books. Gating
        it on Admin would mean the person doing the reconciliation cannot
        record having done it -- while CHANGING the opening balance, which
        moves every figure this account will ever show, stays Admin-only.

        Asserted against the viewset's own branching rather than by
        inventing a user with the right sections, so it keeps holding
        whatever the section-granting mechanism looks like.
        """
        from bankfeeds.views import BankAccountViewSet

        viewset = BankAccountViewSet()
        viewset.action = "confirm_balance"
        on_confirm = [type(p).__name__ for p in viewset.get_permissions()]
        viewset.action = "update"
        on_update = [type(p).__name__ for p in viewset.get_permissions()]

        self.assertNotIn("IsAdmin", on_confirm)
        self.assertIn("IsAdmin", on_update)

    def test_the_confirmed_balance_cannot_be_written_through_the_ordinary_edit(self):
        """It has to carry who looked and when. Writable on the serializer
        it would be a way to mark an account reconciled without anybody
        having looked at a bank."""
        response = self.client.patch(
            f"/api/bank-accounts/{self.account.pk}/",
            {"confirmed_balance": "999999.00"}, format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.account.refresh_from_db()
        self.assertIsNone(self.account.confirmed_balance)


class SerializerTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(
            User.objects.create_user(username="boss", password="x", role="admin", is_staff=True)
        )
        self.account = BankAccount.objects.create(
            name="FNB Cheque", opening_balance=Decimal("1000.00"), opening_balance_date=OPENED,
        )

    def test_money_goes_out_as_strings(self):
        """A JSON float is the one representation a currency amount should
        never travel in, and this would be the only place on the platform
        that did it."""
        row = self.client.get(f"/api/bank-accounts/{self.account.pk}/").json()

        self.assertIsInstance(row["current_balance"], str)
        self.assertEqual(row["current_balance"], "1000.00")

    def test_an_unplaceable_opening_balance_serialises_as_null(self):
        self.account.opening_balance_date = None
        self.account.save(update_fields=["opening_balance_date"])

        row = self.client.get(f"/api/bank-accounts/{self.account.pk}/").json()

        self.assertIsNone(row["current_balance"])
