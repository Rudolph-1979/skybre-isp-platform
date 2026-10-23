"""Job Dispatch: technician codes, pushed jobs, status events, checklists.

Hand-written for the same reason 0001 was: a `makemigrations` run inside
the backend container writes to the container's writable layer, and the
next `docker compose build` throws it away.
"""
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("conecto", "0001_initial"),
        ("scheduling", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ConectoTechnician",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("technician_code", models.CharField(max_length=40, unique=True)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="conecto_technician",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["technician_code"]},
        ),
        migrations.CreateModel(
            name="ConectoJob",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("technician_code", models.CharField(max_length=40)),
                ("conecto_job_id", models.CharField(blank=True, max_length=128)),
                ("last_status", models.CharField(blank=True, max_length=20)),
                ("pushed_at", models.DateTimeField(blank=True, null=True)),
                ("pushed_digest", models.CharField(blank=True, max_length=32)),
                (
                    "job",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="conecto",
                        to="scheduling.job",
                    ),
                ),
            ],
            options={"ordering": ["-pushed_at"]},
        ),
        migrations.CreateModel(
            name="ConectoStockChecklist",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("technician_code", models.CharField(max_length=40)),
                ("effective_date", models.DateField()),
                ("items", models.JSONField(default=list)),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("results", models.JSONField(blank=True, default=list)),
                ("any_missing", models.BooleanField(blank=True, null=True)),
            ],
            options={"ordering": ["-effective_date", "technician_code"]},
        ),
        migrations.AddConstraint(
            model_name="conectostockchecklist",
            constraint=models.UniqueConstraint(
                fields=("technician_code", "effective_date"), name="conecto_checklist_once_per_day"
            ),
        ),
        migrations.CreateModel(
            name="ConectoJobEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("vexar_job_id", models.CharField(blank=True, max_length=64)),
                ("conecto_job_id", models.CharField(blank=True, max_length=128)),
                ("technician_code", models.CharField(blank=True, max_length=40)),
                ("status", models.CharField(max_length=20)),
                ("occurred_at", models.DateTimeField(blank=True, null=True)),
                ("lat", models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True)),
                ("lng", models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True)),
                ("km_traveled", models.DecimalField(blank=True, decimal_places=2, max_digits=8, null=True)),
                ("duration_minutes", models.PositiveIntegerField(blank=True, null=True)),
                ("payload", models.JSONField(blank=True, default=dict)),
                ("received_at", models.DateTimeField(auto_now_add=True)),
                (
                    "job",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="conecto_events",
                        to="scheduling.job",
                    ),
                ),
            ],
            options={"ordering": ["-received_at"]},
        ),
        migrations.AddIndex(
            model_name="conectojobevent",
            index=models.Index(fields=["technician_code", "-received_at"], name="conecto_ev_tech_idx"),
        ),
        migrations.AddIndex(
            model_name="conectojobevent",
            index=models.Index(fields=["vexar_job_id"], name="conecto_ev_job_idx"),
        ),
    ]
