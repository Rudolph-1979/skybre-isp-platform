"""The third model with reminder offsets on it.

0033 made the three day-offsets signed on BillingDefaults and
CustomerBillingConfig, and missed PaymentMethod. All three inherit the
fields from PaymentTermsMixin -- not from RecurringBillingFieldsMixin,
which is what 0033 was written against -- and PaymentMethod inherits
PaymentTermsMixin directly rather than through it.

A separate migration rather than an edit to 0033, deliberately. Unlike the
choices fix in conecto 0007, this one DOES touch the schema: it drops a
CHECK (value >= 0) constraint. Editing an applied migration that changes a
column leaves a database that no longer matches the file that claims to
have built it, and replaying it on a fresh install would produce a
different schema from production. So it goes forward, which is correct
whether or not 0033 has already run.

WHY PaymentMethod NEEDS IT TOO

PaymentMethod is a template: TERMS_FIELDS is copied onto a customer's
config by apply_payment_method. Leaving it positive-only would mean a
payment method could carry every other term but could not carry a chaser
-- and the field it copied into would accept one, so the limitation would
show up as a template that silently cannot express what the target can.
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
        ("billing", "0033_reminder_chasers"),
    ]

    operations = [
        migrations.AlterField(
            model_name="paymentmethod",
            name="reminder_1_day",
            field=models.SmallIntegerField(blank=True, help_text=HELP_1, null=True),
        ),
        migrations.AlterField(
            model_name="paymentmethod",
            name="reminder_2_day",
            field=models.SmallIntegerField(blank=True, help_text=HELP_N, null=True),
        ),
        migrations.AlterField(
            model_name="paymentmethod",
            name="reminder_3_day",
            field=models.SmallIntegerField(blank=True, help_text=HELP_N, null=True),
        ),
    ]
