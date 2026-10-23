# Package #212 (Support dashboard), written by hand.

import datetime

from django.db import migrations, models

import tickets.models


class Migration(migrations.Migration):

    dependencies = [
        ("tickets", "0008_ticket_comment_job_tagged_attachments"),
    ]

    operations = [
        migrations.CreateModel(
            name="SupportSettings",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "reply_target_minutes",
                    models.PositiveIntegerField(
                        default=60, help_text="A first reply within this many office minutes counts as on target."
                    ),
                ),
                ("office_start", models.TimeField(default=datetime.time(8, 0))),
                ("office_end", models.TimeField(default=datetime.time(17, 0))),
                ("office_days", models.JSONField(blank=True, default=tickets.models.default_office_days)),
                (
                    "outage_min_clients",
                    models.PositiveSmallIntegerField(
                        default=5,
                        help_text="This many clients on one access point / router going offline together is a possible outage.",
                    ),
                ),
                (
                    "outage_window_minutes",
                    models.PositiveSmallIntegerField(default=15, help_text="...within this many minutes of each other."),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name_plural": "Support settings",
            },
        ),
    ]
