"""Tariff speeds are Kbps, not Mbps.

Hand-written on purpose. `makemigrations` produced a RemoveField + AddField
pair for this rename, which would have DROPPED every existing speed and left
every tariff blank. RenameField keeps the values, which is what we want: the
numbers already in the database are already Kbps (a 4 Mbps plan is stored as
4096) — it was only the field name, the labels and the arithmetic reading them
that were wrong.

Nothing is multiplied or divided here for that reason. Two places that DID do
the arithmetic wrong are fixed in the same change:

  * radiusauth.signals._mikrotik_rate_limit appended "M" to the stored number,
    so a 4 Mbps plan became "4096M/4096M" — four terabits. RouterOS accepts
    that, which means those customers were never throttled.
  * network.router_sync.effective_speed_kbps multiplied by 1000, shaping the
    same plan to about 4 Gbps.

One thing to check by hand after this runs: any tariff whose speed was typed
as Mbps by mistake now reads as Kbps. "20/5" means 20 Kbps here, not 20 Mbps.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0014_service_auto_suspended_with_customer"),
    ]

    operations = [
        migrations.RenameField(
            model_name="tariff", old_name="speed_download_mbps", new_name="speed_download_kbps",
        ),
        migrations.RenameField(
            model_name="tariff", old_name="speed_upload_mbps", new_name="speed_upload_kbps",
        ),
        migrations.AlterField(
            model_name="tariff",
            name="speed_download_kbps",
            field=models.PositiveIntegerField(
                blank=True, null=True,
                help_text="In Kbps — 4 Mbps is 4096, 10 Mbps is 10240.",
                verbose_name="Download speed (Kbps)",
            ),
        ),
        migrations.AlterField(
            model_name="tariff",
            name="speed_upload_kbps",
            field=models.PositiveIntegerField(
                blank=True, null=True,
                help_text="In Kbps — 4 Mbps is 4096, 10 Mbps is 10240.",
                verbose_name="Upload speed (Kbps)",
            ),
        ),
    ]
