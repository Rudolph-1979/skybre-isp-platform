"""Whether a quoted line is charged once or every month.

Blank by default, and blank is not "unknown" -- it means "decide from the
line's type", which billing.quote_totals.kind_of does: a tariff-plan line
is recurring because accepting the quote turns it into a Service, and
anything else is once-off.

So there is no data migration here, deliberately. Back-filling would
freeze today's guess onto every historical row, and a tariff line added
next year would still need somebody to remember the flag. Deriving it
leaves one source of truth and lets the field mean only what somebody
explicitly chose.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0036_statement_sent_for"),
    ]

    operations = [
        migrations.AddField(
            model_name="invoiceitem",
            name="billing_kind",
            field=models.CharField(
                max_length=10,
                blank=True,
                default="",
                choices=[
                    ("once_off", "Once-off"),
                    ("recurring", "Monthly recurring"),
                ],
                help_text=(
                    "Leave blank to decide from the line type: a tariff plan is "
                    "recurring, anything else is once-off. Set it explicitly on a "
                    "custom line that is one or the other."
                ),
            ),
        ),
    ]
