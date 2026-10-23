"""Bank account balances.

Two fields and one derived figure, but the reasoning matters more than the
code, so it is written down here rather than left in a commit message.

WHAT THE BALANCE IS BUILT FROM. Opening balance, plus every bank
transaction on or after the opening date. NOT payments and expenses --
the bank lines themselves. Those are two different things and only one of
them is the bank: a payment recorded against a customer who paid by card,
an internal transfer between two of these accounts, a bank charge nobody
has captured as an expense yet -- all of those move the real balance, and
a figure built from payments and expenses would miss or double-count
them. The transactions ARE the statement, so a balance built from them is
the statement's balance.

WHY IT CAN DISAGREE WITH THE BANK, AND WHY THAT IS THE POINT. This figure
only matches internet banking if every line is captured. When it doesn't
match, the difference is not noise -- it means a statement has not been
imported, or has been imported twice, or the opening balance was typed
against the wrong date. That is worth surfacing, so the account also
records the balance somebody last confirmed by eye, and the page shows
the variance rather than hiding it.

WHY THE OPENING DATE IS SEPARATE AND REQUIRED-ISH. An opening balance
with no date is meaningless: it has to be the balance immediately BEFORE
the first transaction it is counted alongside, or the first statement's
lines get added to a balance that already included them. Left null, no
balance is shown at all, which is honest -- better a blank than a number
that is confidently a month out.
"""
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bankfeeds", "0002_banktransaction_created_expense_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="bankaccount",
            name="opening_balance",
            field=models.DecimalField(
                decimal_places=2, default=0, max_digits=14,
                help_text=(
                    "The balance on this account immediately before the first transaction "
                    "below. Everything on the feed is added to this."
                ),
            ),
        ),
        migrations.AddField(
            model_name="bankaccount",
            name="opening_balance_date",
            field=models.DateField(
                blank=True, null=True,
                help_text=(
                    "The date that opening balance was true. Transactions on or after this "
                    "date count towards the running balance; earlier ones are assumed to be "
                    "already inside it. No date means no balance is shown."
                ),
            ),
        ),
        migrations.AddField(
            model_name="bankaccount",
            name="confirmed_balance",
            field=models.DecimalField(
                decimal_places=2, max_digits=14, null=True, blank=True,
                help_text="What internet banking actually said, the last time somebody checked.",
            ),
        ),
        migrations.AddField(
            model_name="bankaccount",
            name="confirmed_balance_at",
            field=models.DateTimeField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name="bankaccount",
            name="confirmed_balance_by",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=models.SET_NULL,
                related_name="+", to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
