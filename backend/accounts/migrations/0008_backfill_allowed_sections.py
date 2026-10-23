"""Give every existing non-admin staff account an explicit section list.

This runs in the same deploy as the change that makes an empty list mean
NO access rather than unrestricted. Without it, that change would take
every one of these people's access away at once.

Each account gets the list for its role (see ROLE_SECTIONS below), so
nobody's day changes. What changes is the default for accounts created
afterwards: they start with nothing and have to be granted what they
need, instead of quietly starting with everything including payroll and
the credential store.

Accounts that ALREADY have an explicit list are left alone -- somebody
chose those, and widening them here would be the same bug in reverse.

Admin accounts are skipped: accounts.permissions.user_can_access_section
lets Admin through regardless, so writing a list for them would imply a
restriction that does not exist.

Reversing this clears the lists again, which under the old rule means
unrestricted -- the correct reverse, since rolling the code back restores
the old meaning of empty.
"""
from django.db import migrations

ROLE_SECTIONS = {'technician': ['scheduling', 'tickets', 'customers', 'services', 'networking', 'inventory', 'vehicles'], 'support': ['scheduling', 'tickets', 'customers', 'services', 'networking'], 'sales': ['sales', 'customers', 'services', 'scheduling', 'bulk_email'], 'accounts': ['finance', 'accountant', 'customers', 'services', 'inventory'], 'management': ['scheduling', 'sales', 'customers', 'services', 'finance', 'inventory', 'networking', 'tickets', 'vehicles', 'bulk_email', 'accountant']}


def backfill(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    for user in User.objects.exclude(role="customer").exclude(role="admin"):
        if user.allowed_sections:
            continue
        user.allowed_sections = ROLE_SECTIONS.get(user.role, [])
        user.save(update_fields=["allowed_sections"])


def clear(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    User.objects.exclude(role="customer").exclude(role="admin").update(allowed_sections=[])


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0007_alter_user_allowed_sections"),
    ]

    operations = [
        migrations.RunPython(backfill, clear),
    ]
