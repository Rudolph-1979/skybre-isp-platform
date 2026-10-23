"""The chart of accounts.

WHAT THIS IS, AND WHAT IT IS NOT.

It is the list of accounts the business's money is described in terms of:
the bank accounts, what customers owe, what SARS is owed, income, and the
categories of cost. Suppliers and expenses point at it, so "what did we
spend on bandwidth this year" stops being a text search over a category
field.

It is NOT a double-entry ledger. Nothing here posts a debit against a
credit, so this does not produce a trial balance or a balance sheet, and
it would be dishonest to imply otherwise by shipping an "Accounts" screen
that looks like accounting software. Journals would live in this app,
which is why the app is called `ledger` rather than `chart` -- but they
are not built, and until they are, an account is a label with structure
rather than a balance.

WHY A SEPARATE APP.

Expenses post to accounts. So do invoices, bank accounts and VAT. Putting
the chart inside any one of its consumers makes that one special, and the
first time somebody needs it from elsewhere they import across an app
boundary that should not exist.
"""
from django.db import models


class Account(models.Model):
    """One line of the chart.

    `code` is what accountants actually navigate by, so it is the sort
    order and the thing that must stay unique. It is a CharField rather
    than a number because real charts use codes like "1000/1" and leading
    zeros, and because arithmetic on an account code is never meaningful.
    """

    class Type(models.TextChoices):
        ASSET = "asset", "Asset"
        LIABILITY = "liability", "Liability"
        EQUITY = "equity", "Equity"
        INCOME = "income", "Income"
        EXPENSE = "expense", "Expense"

    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=120)
    type = models.CharField(max_length=20, choices=Type.choices)
    description = models.TextField(blank=True)

    # Accounts the PLATFORM depends on by meaning rather than by number.
    #
    # The VAT control account is not "account 2100" -- it is "the account
    # VAT goes to", and somebody will eventually renumber it to match
    # their accountant's chart, or rename it, and both of those should be
    # allowed. Code that needs it looks it up by system_key, so renaming
    # and renumbering are safe and only deleting is not.
    #
    # Blank for accounts a person added themselves, which the platform has
    # no opinion about and which can be deleted freely.
    system_key = models.CharField(max_length=60, blank=True, db_index=True)

    # Kept rather than deleted, once anything has ever pointed at it.
    # Deactivating hides an account from the pickers without orphaning the
    # expenses already filed against it -- deleting one would either take
    # those with it or silently detach them, and a year-old expense losing
    # its account is a hole in last year's figures.
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} — {self.name}"

    @property
    def is_system(self):
        return bool(self.system_key)


# The system_key values this platform looks accounts up by. Written out
# here rather than as bare strings at each call site, so a typo is an
# ImportError rather than a silently missing account.
BANK = "bank"                      # one per bankfeeds.BankAccount: "bank:<pk>"
DEBTORS = "debtors"
VAT_CONTROL = "vat_control"
EQUITY = "equity"
SERVICE_INCOME = "service_income"
EXPENSE_CATEGORY = "expense"       # one per expenses.Expense.Category: "expense:<value>"


def bank_key(bank_account_id):
    return f"{BANK}:{bank_account_id}"


def expense_key(category):
    return f"{EXPENSE_CATEGORY}:{category}"


def by_key(key):
    """The account for a system_key, or None.

    None rather than an exception on purpose: an account can legitimately
    be missing -- somebody deactivated it, or the seed ran before a
    category existed -- and the callers of this are capturing an expense
    or pre-filling a form. Neither should fail because the chart is
    incomplete; they should just not pre-fill anything.
    """
    return Account.objects.filter(system_key=key, is_active=True).first()
