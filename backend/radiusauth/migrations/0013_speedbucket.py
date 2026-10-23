from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("radiusauth", "0012_radiusnasclient_audit"),
    ]

    operations = [
        migrations.CreateModel(
            name="SpeedBucket",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("username", models.CharField(max_length=255)),
                ("bucket_start", models.DateTimeField()),
                ("download_bytes", models.BigIntegerField(default=0)),
                ("upload_bytes", models.BigIntegerField(default=0)),
            ],
            options={
                "ordering": ["bucket_start"],
                "indexes": [
                    models.Index(fields=["username", "bucket_start"], name="speed_bucket_user_time"),
                    models.Index(fields=["bucket_start"], name="speed_bucket_time"),
                ],
                "constraints": [
                    models.UniqueConstraint(fields=("username", "bucket_start"), name="speed_bucket_unique_slot"),
                ],
            },
        ),
    ]
