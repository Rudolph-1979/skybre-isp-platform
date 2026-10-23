"""A technician link can exist with no code on it.

Hand-written, like every migration in this app: makemigrations inside the
backend container writes to the container's writable layer and the next
build throws it away.

WHY THE UNIQUE INDEX MOVES.

`technician_code` was unique at the column level, which means Postgres
allows many NULLs but only ONE empty string. A blocking record -- a link
with no code, switched off, whose only purpose is to keep somebody off
dispatch -- carries an empty code, so the second one would have failed
with an IntegrityError on a command that has nothing to do with codes.

The condition makes uniqueness say what was always meant: two people may
not share a code, and "no code" is not a code.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("conecto", "0003_technician_positions"),
    ]

    operations = [
        migrations.AlterField(
            model_name="conectotechnician",
            name="technician_code",
            field=models.CharField(blank=True, max_length=40),
        ),
        migrations.AddConstraint(
            model_name="conectotechnician",
            constraint=models.UniqueConstraint(
                fields=["technician_code"],
                condition=~models.Q(technician_code=""),
                name="conecto_technician_code_unique_when_set",
            ),
        ),
    ]
