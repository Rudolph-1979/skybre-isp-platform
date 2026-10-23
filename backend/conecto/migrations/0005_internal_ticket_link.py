"""The link table for internal job-feedback tickets.

Separate from ConectoTicket rather than a column on it, and that is the
whole safety argument.

Conecto's internal and client tickets share one id namespace with no
prefix or range split, and addTicketReply writes to whichever id it is
handed -- it does not check the internal flag. So the thing that must
never happen is a technician's stock query landing in a customer's thread,
or a reply meant for a customer notifying a technician.

With two tables, the lookup is typed by the table it runs against.
_send_ticket_comment asks ConectoTicket for a client ticket's id and
cannot be handed an internal one, because internal ids are not in that
table. No filter, no flag, no remembering.
"""
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("conecto", "0004_technician_code_optional"),
        ("tickets", "0005_internal_tickets"),
        ("scheduling", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="ConectoInternalTicket",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("conecto_ticket_id", models.CharField(max_length=128, unique=True)),
                ("vexar_job_id", models.CharField(blank=True, max_length=64)),
                ("technician_code", models.CharField(blank=True, max_length=40)),
                ("category", models.CharField(blank=True, max_length=32)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "job",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="conecto_internal_tickets",
                        to="scheduling.job",
                    ),
                ),
                (
                    "ticket",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="conecto_internal",
                        to="tickets.ticket",
                    ),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
