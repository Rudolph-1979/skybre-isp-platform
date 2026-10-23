# Written by hand 2026-10-20 (package #207) -- matches what makemigrations
# produces for customers.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("customers", "0023_revenue_snapshot"),
    ]

    operations = [
        migrations.AddField(
            model_name="customer",
            name="equipment_details",
            field=models.TextField(blank=True),
        ),
    ]
