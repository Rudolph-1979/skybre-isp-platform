"""Seed the chart from what this platform already tracks.

WHAT GOES IN, AND WHY IT IS NOT A GENERIC CHART.

The brief was "only what your existing data needs", against "this
platform is becoming the books". Those pull in opposite directions: a
chart seeded from expense categories alone has no asset, liability,
income or equity side and could never balance, while a full generic chart
is a screen of accounts nobody here uses.

So: one account per thing the platform already holds data about.

  * a bank account for each real BankAccount on the feed,
  * customer debtors, because Customer.balance is exactly that,
  * a VAT control account, because the VAT return already computes it,
  * service income, because invoices are raised against it,
  * owner's equity, so the asset side has a counterpart and the chart is
    not lopsided from birth,
  * one expense account per Expense.Category.

The last one goes slightly wider than "categories in use". All nine are
already selectable on the Expenses form, so seeding only the used ones
means the first rent expense anybody books lands on a category with no
account -- a gap created by being frugal about nine rows.

REVERSIBILITY.

Reversing deletes only accounts this migration created (they carry a
system_key), and only where nothing points at them. An account somebody
has since filed expenses against is left alone: taking it away would
either delete those expenses or detach them silently, and a detached
expense is a hole in a VAT period.
"""
from django.db import migrations

EXPENSE_ACCOUNTS = [
    ("6000", "Bandwidth / Transit", "bandwidth"),
    ("6100", "Equipment / Hardware", "equipment"),
    ("6200", "Fuel / Vehicles", "fuel"),
    ("6300", "Rent / Facilities", "rent"),
    ("6400", "Utilities", "utilities"),
    ("6500", "Software / Subscriptions", "software"),
    ("6600", "Professional fees", "professional"),
    ("6700", "Salaries / Payroll", "salaries"),
    ("6900", "Other", "other"),
]


def seed(apps, schema_editor):
    Account = apps.get_model("ledger", "Account")
    BankAccount = apps.get_model("bankfeeds", "BankAccount")

    def ensure(code, name, type_, key, description=""):
        # Matched on system_key, not code: if this is re-run after
        # somebody has renumbered an account to match their accountant's
        # chart, it must recognise it rather than fail on a unique code or
        # create a duplicate.
        if Account.objects.filter(system_key=key).exists():
            return
        if Account.objects.filter(code=code).exists():
            # Somebody already made an account at this number by hand.
            # Leave it entirely alone -- claiming it by stamping a
            # system_key on it would quietly take over a row a person
            # created and change what deleting it is allowed to do.
            return
        Account.objects.create(
            code=code, name=name, type=type_, system_key=key, description=description,
        )

    ensure("1200", "Accounts receivable", "asset", "debtors",
           "What customers owe. Mirrors the balances on the Customers list.")
    ensure("2100", "VAT control", "liability", "vat_control",
           "Output VAT collected less Input VAT claimed. See Accountant -> VAT Returns.")
    ensure("3000", "Owner's equity", "equity", "equity")
    ensure("4000", "Service income", "income", "service_income",
           "Revenue from customer invoices.")

    # One per real bank account, numbered from 1000 in the order they were
    # created, so the numbering is stable rather than alphabetical.
    for offset, bank in enumerate(BankAccount.objects.order_by("id")):
        ensure(
            str(1000 + offset * 10),
            f"Bank — {bank.name}"[:120],
            "asset",
            f"bank:{bank.pk}",
        )

    for code, name, category in EXPENSE_ACCOUNTS:
        ensure(code, name, "expense", f"expense:{category}")


def unseed(apps, schema_editor):
    """Remove only the accounts this seed created, and only the unused ones.

    Looked up defensively: the FKs that point here are added by LATER
    migrations in other apps, so when this one is reversed on its own they
    may not exist in the historical model state yet. Asking for them
    directly would raise rather than reverse.
    """
    Account = apps.get_model("ledger", "Account")

    protected = set()
    for app_label, model_name, field in (
        ("expenses", "Expense", "account"),
        ("inventory", "SupplierCategory", "default_account"),
    ):
        try:
            Model = apps.get_model(app_label, model_name)
        except LookupError:
            continue
        if field not in {f.name for f in Model._meta.get_fields()}:
            continue
        protected |= set(
            Model.objects.exclude(**{field: None}).values_list(f"{field}_id", flat=True)
        )

    Account.objects.exclude(system_key="").exclude(pk__in=protected).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("ledger", "0001_initial"),
        # Needed because the seed reads real bank accounts to name the
        # asset accounts after them.
        ("bankfeeds", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
