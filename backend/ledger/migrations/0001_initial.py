from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True
    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Account",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(max_length=20, unique=True)),
                ("name", models.CharField(max_length=120)),
                ("type", models.CharField(choices=[
                    ("asset", "Asset"), ("liability", "Liability"), ("equity", "Equity"),
                    ("income", "Income"), ("expense", "Expense"),
                ], max_length=20)),
                ("description", models.TextField(blank=True)),
                ("system_key", models.CharField(blank=True, db_index=True, max_length=60)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["code"]},
        ),
    ]
