"""Internal job-feedback tickets: no customer, and a constraint that says so.

A technician filing "quote needed" or "stock needed" from a job creates a
ticket that staff and admins see and a customer never does. Conecto
confirmed on 2026-09-21 that they enforce nothing on their side --
internal and client tickets share one Firestore collection and one id
generator, and addTicketReply will write to either. Their words: "one
guard only -- yours."

So the guard is not a filter somebody has to remember to write. It is the
absence of the customer foreign key, backed by a check constraint:

    is_internal = True   <->   customer IS NULL

The customer portal's queryset is `filter(customer=<their profile>)`, and
NULL never matches anything. An internal ticket cannot appear there even
if every deliberate filter in the codebase were deleted, because there is
no row for the join to find. The constraint is what stops the two halves
drifting apart later -- somebody attaching a customer to an internal
ticket "so we know which site it was" would be rejected by the database
rather than quietly opening the hole.

The reverse direction is enforced too: an ordinary ticket may not lose
its customer.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tickets", "0004_comment_external_author"),
    ]

    operations = [
        migrations.AddField(
            model_name="ticket",
            name="is_internal",
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name="ticket",
            name="customer",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.CASCADE,
                related_name="tickets",
                to="customers.customer",
            ),
        ),
        migrations.AddConstraint(
            model_name="ticket",
            constraint=models.CheckConstraint(
                # `check=` rather than `condition=`: the keyword was renamed
                # in Django 5.1 and the old name works from 2.2 through 5.2.
                # Pinning to the new one would break a deployment on 5.0 for
                # no gain.
                check=(
                    models.Q(is_internal=True, customer__isnull=True)
                    | models.Q(is_internal=False, customer__isnull=False)
                ),
                name="ticket_internal_has_no_customer",
            ),
        ),
    ]
