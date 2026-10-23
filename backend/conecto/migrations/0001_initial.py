"""Written by hand rather than generated in a container.

A `makemigrations` run inside the backend container writes to the
container's writable layer, and the next `docker compose build` throws
that layer away -- which is how tickets/0003_ticket_created_by.py came
to exist only in a running container and take forty tests down with it
when the image was rebuilt. Migrations ship in the package.

Depends on the INITIAL migration of tickets and customers rather than
their current heads: Ticket and Customer both exist from their first
migration, so this is the loosest correct dependency and it will not
conflict with whatever those apps add next.
"""
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("customers", "0001_initial"),
        ("tickets", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="ConectoCall",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("endpoint", models.CharField(max_length=40)),
                ("payload", models.JSONField()),
                ("status", models.CharField(choices=[("ok", "Succeeded"), ("failed", "Failed")], max_length=10)),
                ("http_status", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("detail", models.TextField(blank=True)),
                ("attempts", models.PositiveSmallIntegerField(default=1)),
                ("subject_kind", models.CharField(blank=True, max_length=20)),
                ("subject_ref", models.CharField(blank=True, max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="ConectoLink",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("firebase_uid", models.CharField(max_length=128, unique=True)),
                ("email", models.EmailField(blank=True, max_length=254)),
                ("registered_at", models.DateTimeField(auto_now_add=True)),
                (
                    "customer",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="conecto_link",
                        to="customers.customer",
                    ),
                ),
            ],
            options={"ordering": ["-registered_at"]},
        ),
        migrations.CreateModel(
            name="ConectoTicket",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("conecto_ticket_id", models.CharField(max_length=128, unique=True)),
                (
                    "origin",
                    models.CharField(
                        choices=[("skybre", "Raised in Skybre"), ("conecto", "Raised in the Conecto app")],
                        max_length=10,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "ticket",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="conecto",
                        to="tickets.ticket",
                    ),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex(
            model_name="conectocall",
            index=models.Index(fields=["status", "-created_at"], name="conecto_call_status_idx"),
        ),
    ]
