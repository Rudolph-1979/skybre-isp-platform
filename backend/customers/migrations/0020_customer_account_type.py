from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("customers", "0019_customer_coordinates"),
    ]

    operations = [
        migrations.AddField(
            model_name="customer",
            name="account_type",
            field=models.CharField(
                choices=[("paying", "Paying"), ("sponsor", "Sponsor"), ("deal", "Deal")],
                default="paying", max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="customer",
            name="account_type_reason",
            field=models.CharField(
                blank=True, help_text="Why this client is a sponsor or on a deal. Required unless Paying.",
                max_length=255,
            ),
        ),
    ]
