"""What Conecto were last told about a customer's month.

Hand-written like the rest: makemigrations inside the container writes to
the container's writable layer and the next build discards it.

One table. It is a de-duplication record, not a usage record -- the usage
itself lives in radiusauth.UsageBucket and is not copied here. Storing the
figures a second time would create two answers to "how much did they use
in September", and the one in this table would be whatever was true at the
moment of the last successful push.

They are stored anyway, and only for that purpose: the digest alone would
say "something changed" without ever saying what, which makes a wrong
figure in a customer's app impossible to diagnose from this side.

The unique constraint is in the database rather than in the command,
because two overlapping runs -- a cron that fired while yesterday's was
still going -- would otherwise both decide they were the first.
"""
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("conecto", "0007_documents"),
        ("customers", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="ConectoUsagePush",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("period", models.CharField(max_length=7)),
                ("download_bytes", models.BigIntegerField(default=0)),
                ("upload_bytes", models.BigIntegerField(default=0)),
                ("pushed_digest", models.CharField(blank=True, max_length=32)),
                ("pushed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "customer",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="conecto_usage_pushes",
                        to="customers.customer",
                    ),
                ),
            ],
            options={"ordering": ["-pushed_at"]},
        ),
        migrations.AddConstraint(
            model_name="conectousagepush",
            constraint=models.UniqueConstraint(
                fields=("customer", "period"), name="conecto_usage_once_per_period"
            ),
        ),
    ]
