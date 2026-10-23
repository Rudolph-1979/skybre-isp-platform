# Written by hand 2026-10-21 (package #211) -- matches what makemigrations
# produces for billing; not generated on the server because of the known
# bankfeeds drift (see OUTSTANDING.md).

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0056_servicecancellation"),
        ("customers", "0024_customer_equipment_details"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name="service",
            name="status",
            field=models.CharField(
                choices=[
                    ("active", "Active"), ("suspended", "Suspended"), ("terminated", "Terminated"),
                    ("pending", "Pending Activation"), ("paused", "Paused"),
                ],
                default="pending",
                max_length=20,
            ),
        ),
        migrations.CreateModel(
            name="ServicePause",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("scheduled", "Booked"), ("active", "Paused"), ("ended", "Resumed"), ("withdrawn", "Withdrawn")], default="scheduled", max_length=10)),
                ("pause_from", models.DateField()),
                ("resume_on", models.DateField(blank=True, null=True)),
                ("resumed_on", models.DateField(blank=True, null=True)),
                ("reason", models.CharField(max_length=500)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("ended_at", models.DateTimeField(blank=True, null=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("credit_requests", models.ManyToManyField(blank=True, related_name="service_pauses", to="billing.creditrequest")),
                ("customer", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="service_pauses", to="customers.customer")),
                ("ended_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("service", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="pauses", to="billing.service")),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
