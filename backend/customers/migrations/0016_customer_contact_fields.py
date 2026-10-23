from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("customers", "0015_customer_complex_or_area_alter_customer_address"),
    ]

    operations = [
        migrations.AddField(
            model_name="customer",
            name="contact_person",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="customer",
            name="contact_person_phone",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="customer",
            name="contact_person_email",
            field=models.EmailField(blank=True, default="", max_length=254),
        ),
        migrations.AddField(
            model_name="customer",
            name="secondary_contact_name",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="customer",
            name="secondary_contact_phone",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
    ]