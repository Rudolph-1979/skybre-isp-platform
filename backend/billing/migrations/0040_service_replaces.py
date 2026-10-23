# Generated for package #22: a new service can take over its predecessor's
# PPPoE login and IP at cutover (billing.scheduled_changes.apply_service_handover).

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0039_service_activate_on"),
    ]

    operations = [
        migrations.AddField(
            model_name="service",
            name="replaces",
            field=models.OneToOneField(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name="replaced_by", to="billing.service",
                help_text="The service this one takes over from -- carries over its PPPoE login and IP at cutover.",
            ),
        ),
    ]
