"""Name the author of a ticket comment that came from outside.

A reply from the Conecto app is written by the customer, who has no user
account here, so TicketComment.author is null for it. Null is already how
a platform-generated comment looks, so without this field a customer's
own words appear on the ticket with nothing to say they are the
customer's.

Blank, not null: every existing comment was written by a staff user and
"no external author" is a real answer for them, not a missing one.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tickets", "0003_ticket_created_by"),
    ]

    operations = [
        migrations.AddField(
            model_name="ticketcomment",
            name="external_author",
            field=models.CharField(blank=True, max_length=150),
        ),
    ]
