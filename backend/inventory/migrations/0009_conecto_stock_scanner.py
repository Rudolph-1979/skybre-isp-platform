# Generated for the Conecto stock scanner, 12 Oct 2026.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0008_supplier_categories"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="stockreceipt",
            name="conecto_receipt_id",
            field=models.CharField(blank=True, editable=False, max_length=100, null=True, unique=True),
        ),
        migrations.CreateModel(
            name="ProductBarcode",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("barcode", models.CharField(max_length=64, unique=True)),
                (
                    "source",
                    models.CharField(
                        choices=[("staff", "Added here"), ("conecto", "Linked from the Conecto scanner")],
                        default="staff",
                        max_length=10,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "added_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "product",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="barcodes",
                        to="inventory.product",
                    ),
                ),
            ],
            options={
                "ordering": ["created_at", "pk"],
            },
        ),
    ]
