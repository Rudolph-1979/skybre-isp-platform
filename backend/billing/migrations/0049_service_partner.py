import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("customers", "0019_customer_coordinates"),
        ("billing", "0048_service_site_coordinates"),
    ]

    operations = [
        migrations.AddField(
            model_name="service",
            name="partner",
            field=models.ForeignKey(
                blank=True, help_text="Blank = same as the client's partner.", null=True,
                on_delete=django.db.models.deletion.SET_NULL, related_name="partner_services",
                to="customers.partner",
            ),
        ),
    ]
