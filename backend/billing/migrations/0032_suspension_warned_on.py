"""suspension_warned_for -> suspension_warned_on. The meaning changed again.

0031 made the field hold the CUT-OFF DATE warned about, because warnings
were one email per cut-off and a per-day dedupe would have sent one every
day of the notice period.

That is now the wanted behaviour: a daily countdown -- "3 days", "2
days", "1 day" -- because a single email naming a date is too easy to
delete or lose in spam, and nothing follows it. So the dedupe is per day
again, and the field holds the day a warning was last sent.

A rename rather than add-and-drop: the column keeps what is in it, which
is nothing -- no warning has been sent yet.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0031_suspension_warned_for"),
    ]

    operations = [
        migrations.RenameField(
            model_name="customerbillingconfig",
            old_name="suspension_warned_for",
            new_name="suspension_warned_on",
        ),
    ]
