from django.db import migrations, models

# The six types that used to be hard-coded, with the colours the calendar
# already showed them in, so nothing changes on screen after migrating.
BUILT_IN = [
    ("installation", "Installation", "#2a78d6"),
    ("repair", "Repair", "#e34948"),
    ("maintenance", "Maintenance", "#eda100"),
    ("site_visit", "Site Visit", "#1baf7a"),
    ("office_task", "Office Task", "#4a3aa7"),
    ("other", "Other", "#898781"),
]


def seed(apps, schema_editor):
    JobType = apps.get_model("scheduling", "JobType")
    Job = apps.get_model("scheduling", "Job")
    for order, (key, name, colour) in enumerate(BUILT_IN, start=1):
        JobType.objects.get_or_create(key=key, defaults={"name": name, "colour": colour, "sort_order": order * 10})
    # Belt and braces: any value already on a job gets a row too.
    for key in Job.objects.values_list("job_type", flat=True).distinct():
        if key and not JobType.objects.filter(key=key).exists():
            JobType.objects.create(key=key, name=key.replace("_", " ").title()[:60])


class Migration(migrations.Migration):
    dependencies = [
        ("scheduling", "0004_alter_job_end_alter_job_start"),
    ]

    operations = [
        migrations.CreateModel(
            name="JobType",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.SlugField(max_length=50, unique=True)),
                ("name", models.CharField(max_length=60, unique=True)),
                ("colour", models.CharField(default="#898781", help_text="Calendar colour, #rrggbb.", max_length=7)),
                ("is_active", models.BooleanField(default=True)),
                ("sort_order", models.PositiveIntegerField(default=100)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["sort_order", "name"]},
        ),
        migrations.AlterField(
            model_name="job",
            name="job_type",
            field=models.CharField(default="site_visit", max_length=50),
        ),
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
