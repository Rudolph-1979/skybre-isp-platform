"""The warning before the cut-off.

Four fields:

  * SuspensionSettings.warning_enabled / warning_days -- the switch and
    the notice period. warning_enabled defaults to TRUE, unlike
    auto_suspend_enabled which defaults to off. Sending somebody a warning
    is not a destructive act; not sending one is the failure this closes,
    and a platform that had to be told to warn people would keep not
    warning them.
  * CustomerBillingConfig.last_suspension_warning_date -- one warning per
    customer per run date, so re-running a Run does not re-send it.
  * RecurringBillingRun.suspension_warnings_sent_count -- so History says
    how many went out, the same way it does for reminders.

Hand-written rather than generated in the container: a makemigrations run
inside the backend writes to the container's writable layer and the next
build throws it away.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0029_tariff_partner"),
    ]

    operations = [
        migrations.AddField(
            model_name="suspensionsettings",
            name="warning_enabled",
            field=models.BooleanField(
                default=True,
                help_text=(
                    "Email customers before their line is suspended, naming the date. "
                    "Independent of the master switch above: warnings can run while "
                    "auto-suspension is still off."
                ),
            ),
        ),
        migrations.AddField(
            model_name="suspensionsettings",
            name="warning_days",
            field=models.PositiveSmallIntegerField(
                default=3,
                help_text=(
                    "How many days before the cut-off the warning goes out. 3 means a customer "
                    "due to be suspended on the 25th is warned on the 22nd."
                ),
            ),
        ),
        migrations.AddField(
            model_name="customerbillingconfig",
            name="last_suspension_warning_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="recurringbillingrun",
            name="suspension_warnings_sent_count",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
