from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("radiusauth", "0004_ovpnclientconnection"),
    ]

    operations = [
        migrations.CreateModel(
            name="SessionUsageSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("acctuniqueid", models.CharField(max_length=32, unique=True)),
                ("username", models.CharField(db_index=True, max_length=255)),
                ("last_input_octets", models.BigIntegerField(default=0)),
                ("last_output_octets", models.BigIntegerField(default=0)),
                ("last_change_at", models.DateTimeField()),
                ("input_bps", models.BigIntegerField(default=0)),
                ("output_bps", models.BigIntegerField(default=0)),
                ("sampled_at", models.DateTimeField()),
            ],
        ),
        migrations.AddIndex(
            model_name="sessionusagesnapshot",
            index=models.Index(fields=["username"], name="radiusauth__usernam_5a9c2f_idx"),
        ),
    ]
