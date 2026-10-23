"""Live bandwidth sessions for the Conecto app.

Hand-written like every migration in this app: makemigrations inside the
backend container writes to the container's writable layer and the next
build throws it away.

A row per viewing session, created when a client opens the speed screen
and finished when they leave it -- or when the five minutes run out,
whichever comes first. The expiry is stored rather than computed so the
cutoff survives a restart and can be read straight out of the table when
somebody asks why a session stopped.
"""
import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("conecto", "0005_internal_ticket_link"),
        ("customers", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="ConectoBandwidthSession",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("session_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("started_at", models.DateTimeField(auto_now_add=True)),
                ("expires_at", models.DateTimeField()),
                ("last_polled_at", models.DateTimeField(blank=True, null=True)),
                ("stopped_at", models.DateTimeField(blank=True, null=True)),
                ("poll_count", models.PositiveIntegerField(default=0)),
                (
                    "customer",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="conecto_bandwidth_sessions",
                        to="customers.customer",
                    ),
                ),
            ],
            options={"ordering": ["-started_at"]},
        ),
        migrations.AddIndex(
            model_name="conectobandwidthsession",
            index=models.Index(
                fields=["customer", "-started_at"], name="conecto_bw_customer_idx"
            ),
        ),
    ]
