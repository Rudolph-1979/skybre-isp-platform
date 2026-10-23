"""What a fill-up cost.

Nullable, and staying nullable: every fill-up logged before this field
existed has no cost, and a default of 0 would put those into the monthly
spend as free tanks of fuel. Vehicle.fuel_summary counts nulls and
reports them separately rather than adding them in.

Hand-written rather than generated in the container -- a makemigrations
run inside the backend writes to the container's writable layer and the
next `docker compose build` throws it away.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("fleet", "0004_vehicle_in_service_since"),
    ]

    operations = [
        migrations.AddField(
            model_name="fuellog",
            name="cost",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text="The total on the slip, VAT included. Leave blank if it isn't known.",
                max_digits=10,
                null=True,
                verbose_name="Total paid (R)",
            ),
        ),
    ]
