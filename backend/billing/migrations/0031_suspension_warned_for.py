"""Rename last_suspension_warning_date -> suspension_warned_for.

Not cosmetic: the field's MEANING changed. It held the day a warning was
sent; it now holds the cut-off date that was warned about.

Warnings used to fire on exactly the day a cut-off was warning_days away,
so "the day we sent" and "which cut-off" were interchangeable. They now
fire for anyone due to be cut off within the next warning_days, which
makes a customer eligible every day until the date arrives -- deduping on
the send day would email them daily for the whole window. Deduping on the
date warned about gives one email per cut-off.

A rename rather than add-and-drop, so the column keeps whatever was
already in it. In practice that is nothing: the field is a day old and no
warning has been sent yet.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0030_suspension_warning"),
    ]

    operations = [
        migrations.RenameField(
            model_name="customerbillingconfig",
            old_name="last_suspension_warning_date",
            new_name="suspension_warned_for",
        ),
    ]
