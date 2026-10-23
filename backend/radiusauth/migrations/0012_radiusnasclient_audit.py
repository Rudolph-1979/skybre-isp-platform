"""Audit trail and last-changed stamp for RADIUS NAS clients.

See RadiusNasClientAudit in models.py for why. Short version: the
2026-09-20 outage was caused by a change to these rows, and the platform
had no record of who made it, when, or what it was.

updated_at is nullable rather than defaulted. A default would stamp every
existing row with the date of this deploy, which reads as "all seven NAS
clients were edited on the day we added the audit trail" -- a plausible
looking lie in exactly the table people will consult after the next
outage. Null means "not known", which is the truth for rows that predate
this.
"""
import django.contrib.postgres.fields
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("radiusauth", "0011_speedwindow"),
    ]

    operations = [
        migrations.AddField(
            model_name="radiusnasclient",
            name="updated_at",
            field=models.DateTimeField(auto_now=True, null=True, blank=True),
        ),
        migrations.AddField(
            model_name="radiusnasclient",
            name="changed_by",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name="+", to=settings.AUTH_USER_MODEL,
                help_text="Who last saved this client. Null for changes made by code rather than by a person.",
            ),
        ),
        migrations.CreateModel(
            name="RadiusNasClientAudit",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("client_pk", models.IntegerField(blank=True, null=True)),
                ("shortname", models.CharField(blank=True, max_length=64)),
                ("action", models.CharField(choices=[("created", "Created"), ("updated", "Updated"), ("deleted", "Deleted")], max_length=10)),
                ("changed_fields", django.contrib.postgres.fields.ArrayField(
                    base_field=models.CharField(max_length=64), blank=True, default=list, size=None)),
                ("changed_at", models.DateTimeField(auto_now_add=True)),
                ("changed_by", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name="+", to=settings.AUTH_USER_MODEL)),
                ("client", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name="audit_entries", to="radiusauth.radiusnasclient")),
            ],
            options={"ordering": ["-changed_at"]},
        ),
    ]
