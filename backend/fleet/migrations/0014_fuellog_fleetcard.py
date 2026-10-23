from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("fleet", "0013_trip_places"),
    ]

    operations = [
        migrations.AlterField(
            model_name="fuellog",
            name="source",
            field=models.CharField(
                choices=[("staff", "Logged here"), ("conecto", "Conecto app"), ("fleetcard", "FNB Fleetcard")],
                default="staff",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="fuellog",
            name="fleetcard_ref",
            field=models.CharField(blank=True, max_length=64, null=True, unique=True),
        ),
    ]
