from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tickets", "0006_ticket_escalation"),
    ]

    operations = [
        migrations.AddField(
            model_name="ticket",
            name="disqualified_from_kpi",
            field=models.BooleanField(default=False),
        ),
    ]
