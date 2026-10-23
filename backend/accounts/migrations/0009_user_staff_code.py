"""Staff codes, and one for every staff account that already exists.

The code is what Conecto gates app registration on. Backfilled here
rather than left to be assigned on each account's next save, because
"next save" for a staff member who is not being edited is never, and they
would simply be unable to register with no visible reason why.

Derived from the primary key, matching User.save(). Customers are left
null: they have customer_id, and a unique column takes many NULLs but
only one empty string.
"""
from django.db import migrations, models


def assign_codes(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    # The historical model has no is_staff_member property, so the rule it
    # encodes -- everything except Customer is staff -- is written out.
    for user in User.objects.exclude(role="customer").filter(staff_code__isnull=True):
        user.staff_code = f"STF-{user.pk:06d}"
        user.save(update_fields=["staff_code"])


def clear_codes(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    User.objects.update(staff_code=None)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0008_backfill_allowed_sections"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="staff_code",
            field=models.CharField(
                blank=True, max_length=20, null=True, unique=True,
                help_text="Identifier shared with the Conecto app. Assigned automatically to staff accounts.",
            ),
        ),
        migrations.RunPython(assign_codes, clear_codes),
    ]
