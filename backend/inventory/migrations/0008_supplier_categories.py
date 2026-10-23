"""Supplier categories, and the category on a supplier.

A category exists to carry a default expense account, so capturing an
invoice from a supplier already knows where it belongs. It is only ever a
default: the account that counts is stored on the expense itself, so
re-categorising a supplier next year cannot re-post invoices already
declared in a filed VAT period.
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0007_stocksite"),
        ("ledger", "0002_seed_accounts"),
    ]

    operations = [
        migrations.CreateModel(
            name="SupplierCategory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100, unique=True)),
                ("notes", models.TextField(blank=True)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("default_account", models.ForeignKey(
                    blank=True, null=True,
                    limit_choices_to={"type": "expense", "is_active": True},
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="supplier_categories", to="ledger.account",
                    help_text="Pre-fills the account when capturing an expense from a supplier in this category.",
                )),
            ],
            options={"ordering": ["name"], "verbose_name_plural": "supplier categories"},
        ),
        migrations.AddField(
            model_name="supplier",
            name="category",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name="suppliers", to="inventory.suppliercategory",
            ),
        ),
    ]
