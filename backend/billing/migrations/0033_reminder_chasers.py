"""Let a payment reminder chase an invoice that is already late.

Hand-written like the rest: makemigrations inside the container writes to
the container's writable layer and the next build discards it.

Six AlterFields, no data change. The three reminder day-offsets move from
PositiveSmallIntegerField to SmallIntegerField on both concrete models
that inherit RecurringBillingFieldsMixin -- BillingDefaults, the template,
and CustomerBillingConfig, the per-customer copy of it.

WHY

The engine decides whether a reminder is due with

    (invoice.date_due - run_date).days in offsets

which is a signed number: positive is before the due date, negative after
it. The arithmetic has always handled both. The column could only hold the
first, so an overdue chaser was not a feature anybody had turned off -- it
was a number the database refused to store, while the settings screen
described it as configurable.

NOTHING IS BACK-FILLED, deliberately. Every existing offset is a positive
number and keeps meaning exactly what it meant: fire that many days before
the due date. Turning any of them into a chaser is a decision somebody
makes per customer, having thought about where it lands relative to their
blocking period -- a chaser set further out than the blocking period
arrives after the line is already off.

Postgres widens smallint to smallint here (both are smallint; only the
CHECK constraint on >= 0 goes away), so this is a metadata change on a
tiny table and takes no meaningful lock.
"""
from django.db import migrations, models


HELP_1 = (
    "Days from the due date. Positive fires before it (7 = a week's "
    "notice), negative after it (-7 = chased a week late). Blank "
    "disables this slot."
)
HELP_N = "As above. Positive is before the due date, negative after it."


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0032_suspension_warned_on"),
    ]

    operations = [
        migrations.AlterField(
            model_name="billingdefaults",
            name="reminder_1_day",
            field=models.SmallIntegerField(blank=True, help_text=HELP_1, null=True),
        ),
        migrations.AlterField(
            model_name="billingdefaults",
            name="reminder_2_day",
            field=models.SmallIntegerField(blank=True, help_text=HELP_N, null=True),
        ),
        migrations.AlterField(
            model_name="billingdefaults",
            name="reminder_3_day",
            field=models.SmallIntegerField(blank=True, help_text=HELP_N, null=True),
        ),
        migrations.AlterField(
            model_name="customerbillingconfig",
            name="reminder_1_day",
            field=models.SmallIntegerField(blank=True, help_text=HELP_1, null=True),
        ),
        migrations.AlterField(
            model_name="customerbillingconfig",
            name="reminder_2_day",
            field=models.SmallIntegerField(blank=True, help_text=HELP_N, null=True),
        ),
        migrations.AlterField(
            model_name="customerbillingconfig",
            name="reminder_3_day",
            field=models.SmallIntegerField(blank=True, help_text=HELP_N, null=True),
        ),
    ]
