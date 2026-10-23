"""Who suspended this service, and may therefore un-suspend it.

`auto_suspended_with_customer` already answers "the customer cascade did
this", and its own comment says why that mark has to exist: without it,
reactivating a customer would hand back internet to somebody who still
has not paid.

The billing run needed the same mark for the opposite direction and never
had one. It suspended a service and left nothing behind saying so, which
is why no payment has ever reconnected anybody.

Default False, and False is the safe answer: a service with no mark is
one this platform will not turn back on by itself. Nothing is
back-filled -- a historical suspension cannot be distinguished from a
manual one by looking at the row, and guessing here would hand automatic
reconnection to a line somebody cut off on purpose. The one-off sweep in
`apply_reactivations --include-unflagged` exists for those, prints them,
and changes nothing without --commit.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0037_invoiceitem_billing_kind"),
    ]

    operations = [
        migrations.AddField(
            model_name="service",
            name="suspended_for_nonpayment",
            field=models.BooleanField(default=False, editable=False),
        ),
    ]
