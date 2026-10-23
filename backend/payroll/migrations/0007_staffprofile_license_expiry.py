"""The driver's licence expiry date on a staff profile.

`license_number` has been here since staff who drive company vehicles
needed their card on file. The number alone never told anyone when it runs
out, so it could sit on the record for five years and quietly go invalid.
This is the date beside it.

Nullable and not backfilled, for the same reason as the vehicle disc: an
invented date reads as compliant, and a blank one reads as "go and ask
them", which is true.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("payroll", "0006_payrollsettings_payrollrunline_additional_amount_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="staffprofile",
            name="license_expiry",
            field=models.DateField(
                blank=True,
                null=True,
                help_text="The expiry date printed on the card. A South African card runs five years.",
                verbose_name="Driver's licence expires",
            ),
        ),
    ]
