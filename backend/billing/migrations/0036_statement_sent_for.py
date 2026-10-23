"""Which month a customer was last sent a statement for.

Hand-written like the rest: makemigrations inside the container writes to
the container's writable layer and the next build discards it.

One nullable-ish CharField on CustomerBillingConfig, blank by default, so
nothing is back-filled and the first run after this treats everybody as
not yet sent.

KEYED ON THE PERIOD, NOT ON A SEND DATE. A date records that something was
attempted on a particular day, which a re-run the following day sails
straight past, and which a failed send sets just as readily as a
successful one. The period is the thing actually being deduplicated --
this customer, this month, once -- and the command writes it only after
the mailer confirms the send.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0035_public_holiday"),
    ]

    operations = [
        migrations.AddField(
            model_name="customerbillingconfig",
            name="statement_sent_for",
            field=models.CharField(blank=True, max_length=7),
        ),
    ]
