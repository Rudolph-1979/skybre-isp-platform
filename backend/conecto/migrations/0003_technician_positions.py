"""Periodic GPS pings from a technician's phone while en route.

Hand-written for the same reason 0001 and 0002 were: a `makemigrations`
run inside the backend container writes to the container's writable
layer, and the next `docker compose build` throws it away.

The unique constraint on (technician_code, recorded_at) is not
housekeeping -- it is the idempotency guard. Conecto retries nothing, but
proxies and operators replay, and a phone that was out of signal flushes
its backlog when it reconnects. The same reading twice is the same
reading, and the database is a better place to say so than a receiver
that has to remember to check.
"""
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("conecto", "0002_job_dispatch"),
        ("scheduling", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="ConectoTechnicianPosition",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("technician_code", models.CharField(max_length=40)),
                ("vexar_job_id", models.CharField(blank=True, max_length=64)),
                ("conecto_job_id", models.CharField(blank=True, max_length=128)),
                ("lat", models.DecimalField(decimal_places=6, max_digits=9)),
                ("lng", models.DecimalField(decimal_places=6, max_digits=9)),
                ("recorded_at", models.DateTimeField()),
                ("received_at", models.DateTimeField(auto_now_add=True)),
                ("payload", models.JSONField(blank=True, default=dict)),
                (
                    "job",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="conecto_positions",
                        to="scheduling.job",
                    ),
                ),
            ],
            options={"ordering": ["-recorded_at"]},
        ),
        migrations.AddIndex(
            model_name="conectotechnicianposition",
            index=models.Index(
                fields=["technician_code", "-recorded_at"], name="conecto_pos_tech_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="conectotechnicianposition",
            index=models.Index(fields=["job", "-recorded_at"], name="conecto_pos_job_idx"),
        ),
        migrations.AddConstraint(
            model_name="conectotechnicianposition",
            constraint=models.UniqueConstraint(
                fields=["technician_code", "recorded_at"], name="conecto_position_once"
            ),
        ),
    ]
