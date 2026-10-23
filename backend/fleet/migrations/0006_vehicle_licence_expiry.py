"""The vehicle licence disc's expiry date.

Nullable, and null is left to mean "nobody has captured this yet" rather
than being backfilled with a guess. A made-up date would put every vehicle
on the platform onto the compliant list on day one, which is the one
outcome this feature exists to prevent -- an empty date shows up as
`unknown` on the Renewals tab and is somebody's job to go and read off the
disc.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("fleet", "0005_fuellog_cost"),
    ]

    operations = [
        migrations.AddField(
            model_name="vehicle",
            name="licence_expiry",
            field=models.DateField(
                blank=True,
                null=True,
                help_text="The date on the current licence disc. Renewing sets this a year on.",
                verbose_name="Licence disc expires",
            ),
        ),
    ]
