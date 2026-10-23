"""Expense.account, and every existing expense mapped onto the chart.

The backfill reads each expense's category and files it against the
matching seeded account. It is exact -- every Expense.Category has an
account with system_key "expense:<category>" -- so nothing is guessed and
nothing is left unmapped.

Only expenses with no account are touched, so re-running this cannot
overwrite an account somebody has since corrected by hand. Reversing
clears the column, which is what it held before.
"""
import django.db.models.deletion
from django.db import migrations, models


def map_categories(apps, schema_editor):
    Account = apps.get_model("ledger", "Account")
    Expense = apps.get_model("expenses", "Expense")

    by_category = {
        account.system_key.split(":", 1)[1]: account
        for account in Account.objects.filter(system_key__startswith="expense:")
    }
    for expense in Expense.objects.filter(account__isnull=True):
        account = by_category.get(expense.category)
        if account is None:
            continue
        expense.account = account
        expense.save(update_fields=["account"])


def unmap(apps, schema_editor):
    apps.get_model("expenses", "Expense").objects.update(account=None)


class Migration(migrations.Migration):

    dependencies = [
        ("expenses", "0002_alter_expense_amount_excl_vat_and_more"),
        ("ledger", "0002_seed_accounts"),
    ]

    operations = [
        migrations.AddField(
            model_name="expense",
            name="account",
            field=models.ForeignKey(
                blank=True, null=True,
                limit_choices_to={"type": "expense"},
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="expenses", to="ledger.account",
            ),
        ),
        migrations.RunPython(map_categories, unmap),
    ]
