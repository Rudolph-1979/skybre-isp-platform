from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0013_user_dashboard_layout"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="panel_state",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text='Open panels per page: {"vehicle": [panel keys]}. A page not listed = its default.',
            ),
        ),
    ]
