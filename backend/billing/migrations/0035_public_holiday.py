"""Days the platform will not disconnect anybody on.

Hand-written like the rest: makemigrations inside the container writes to
the container's writable layer and the next build discards it.

One table, no data. `seed_public_holidays` fills it, and every row stays
editable afterwards -- the seed is a starting point, not the authority.
Election days get declared at three weeks' notice and a company shutdown
is nobody's statute, so this could not be a hard-coded list.

Empty is SAFE, not broken: with no rows, business days are simply Monday
to Friday and a cut-off never lands on a weekend. Public holidays go
unrecognised until somebody seeds them, which is a smaller failure than
the one this replaces.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0034_payment_method_chasers"),
    ]

    operations = [
        migrations.CreateModel(
            name="PublicHoliday",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("date", models.DateField(unique=True)),
                ("name", models.CharField(max_length=120)),
                (
                    "seeded",
                    models.BooleanField(
                        default=False,
                        help_text=(
                            "Created by seed_public_holidays. Rows added by hand stay "
                            "untouched by a re-seed."
                        ),
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "verbose_name": "Public holiday",
                "verbose_name_plural": "Public holidays",
                "ordering": ["date"],
            },
        ),
    ]
