import uuid

from django.db import migrations, models


def backfill_tokens(apps, schema_editor):
    """Give every existing customer its own token.

    A plain AddField with default=uuid.uuid4 evaluates the default ONCE and
    writes the same value to every row, which then fails the unique
    constraint (or worse, silently gives every customer the same usage
    link). So the column goes on nullable and non-unique, gets filled row
    by row, and only then becomes unique.
    """
    Customer = apps.get_model("customers", "Customer")
    for pk in Customer.objects.values_list("pk", flat=True).iterator():
        Customer.objects.filter(pk=pk).update(usage_token=uuid.uuid4())


class Migration(migrations.Migration):

    dependencies = [
        ("customers", "0006_customer_signup_date"),
    ]

    operations = [
        migrations.AddField(
            model_name="customer",
            name="usage_token",
            field=models.UUIDField(null=True, editable=False),
        ),
        migrations.RunPython(backfill_tokens, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="customer",
            name="usage_token",
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
    ]
