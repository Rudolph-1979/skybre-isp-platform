# "Rand amount up" special pricing (13 Oct 2026) -- choices only, no data change.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0052_tariff_cost_price"),
    ]

    operations = [
        migrations.AlterField(
            model_name="service",
            name="discount_type",
            field=models.CharField(
                choices=[
                    ("none", "No discount"),
                    ("percentage", "Percentage off"),
                    ("amount", "Rand amount off"),
                    ("surcharge", "Rand amount up"),
                ],
                default="none",
                help_text="Percentage off the plan price, a flat rand amount off, or none.",
                max_length=12,
            ),
        ),
    ]
