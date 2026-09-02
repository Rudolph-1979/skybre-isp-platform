import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("network", "0001_initial"),
        ("radiusauth", "0005_sessionusagesnapshot"),
    ]

    operations = [
        migrations.CreateModel(
            name="RouterLiveRate",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("username", models.CharField(max_length=255, unique=True)),
                ("interface", models.CharField(blank=True, max_length=64)),
                ("last_rx_byte", models.BigIntegerField(default=0)),
                ("last_tx_byte", models.BigIntegerField(default=0)),
                ("download_bps", models.BigIntegerField(default=0)),
                ("upload_bps", models.BigIntegerField(default=0)),
                ("sampled_at", models.DateTimeField()),
                (
                    "device",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="live_rates",
                        to="network.device",
                    ),
                ),
            ],
        ),
        migrations.AddIndex(
            model_name="routerliverate",
            index=models.Index(fields=["username"], name="radiusauth__usernam_7c31d4_idx"),
        ),
    ]
