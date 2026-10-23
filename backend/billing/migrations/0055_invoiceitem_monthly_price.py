# Written by hand 2026-10-16 (package #183) -- matches what makemigrations
# produces for billing; not generated on the server because of the known
# bankfeeds drift (see OUTSTANDING.md).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0054_customer_charge_pricing"),
    ]

    operations = [
        migrations.AlterField(
            model_name="invoiceitem",
            name="billing_kind",
            field=models.CharField(
                blank=True,
                choices=[("once_off", "Once-off"), ("recurring", "Monthly recurring")],
                default="",
                help_text="Leave blank to decide from the line type: a tariff plan or a recurring charge is recurring, anything else is once-off. Set it explicitly on a custom line that is one or the other.",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="invoiceitem",
            name="monthly_price",
            field=models.DecimalField(blank=True, decimal_places=4, max_digits=12, null=True),
        ),
    ]
