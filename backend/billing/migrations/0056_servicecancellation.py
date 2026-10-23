# Written by hand 2026-10-20 (package #203) -- matches what makemigrations
# produces for billing; not generated on the server because of the known
# bankfeeds drift (see OUTSTANDING.md).

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0055_invoiceitem_monthly_price"),
        ("customers", "0019_customer_coordinates"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ServiceCancellation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("kind", models.CharField(choices=[("notice", "Client gave notice"), ("cannot_service", "Can't be serviced — moved to a new service")], max_length=20)),
                ("status", models.CharField(choices=[("active", "Booked"), ("withdrawn", "Withdrawn")], default="active", max_length=10)),
                ("notice_date", models.DateField(blank=True, null=True)),
                ("end_date", models.DateField()),
                ("previous_end_date", models.DateField(blank=True, null=True)),
                ("reason", models.CharField(max_length=500)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("withdrawn_at", models.DateTimeField(blank=True, null=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("credit_requests", models.ManyToManyField(blank=True, related_name="service_cancellations", to="billing.creditrequest")),
                ("customer", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="service_cancellations", to="customers.customer")),
                ("replacement_service", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="replaces_cancelled", to="billing.service")),
                ("service", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="cancellations", to="billing.service")),
                ("withdrawn_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
