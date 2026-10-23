# Recurring charges move from the service to the customer (Rudolph, 1 Oct:
# "some clients only take a domain, email or web hosting").
#
# Order matters: create the new table, copy every existing
# Service.recurring_charges link across, THEN drop the old M2M -- so no
# charge anyone is being billed for is lost on the way.

import django.db.models.deletion
from django.db import migrations, models
from django.utils import timezone


def copy_service_charges_to_customers(apps, schema_editor):
    Service = apps.get_model("billing", "Service")
    CustomerRecurringCharge = apps.get_model("billing", "CustomerRecurringCharge")
    Through = Service.recurring_charges.through
    today = timezone.localdate()
    for row in Through.objects.select_related("service").order_by("pk"):
        service = row.service
        CustomerRecurringCharge.objects.create(
            customer_id=service.customer_id,
            charge_id=row.recurringcharge_id,
            quantity=1,
            # Billed from whenever the service was; ends when the service
            # was already booked to end, so nothing bills that wouldn't
            # have before. After this it is the client's, not the line's.
            start_date=service.start_date or today,
            end_date=service.end_date,
            note="",
        )


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0044_creditrequest_invoice"),
        ("customers", "0017_customer_pending_partner"),
    ]

    operations = [
        migrations.CreateModel(
            name="CustomerRecurringCharge",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("quantity", models.PositiveIntegerField(default=1)),
                ("start_date", models.DateField()),
                (
                    "end_date",
                    models.DateField(
                        blank=True, null=True,
                        help_text="First day it is no longer billed. Blank = until stopped.",
                    ),
                ),
                ("note", models.CharField(blank=True, help_text="e.g. the domain name.", max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "charge",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="customer_links",
                        to="billing.recurringcharge",
                    ),
                ),
                (
                    "customer",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="customer_charges",
                        to="customers.customer",
                    ),
                ),
            ],
            options={"ordering": ["charge__name", "pk"]},
        ),
        migrations.RunPython(copy_service_charges_to_customers, migrations.RunPython.noop),
        migrations.RemoveField(model_name="service", name="recurring_charges"),
    ]
