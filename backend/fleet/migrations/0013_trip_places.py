from django.db import migrations, models


def places_from_notes(apps, schema_editor):
    """Webfleet trips synced before #191 have their places only in the note
    "Webfleet: <start> → <stop>"; copy them out (the note stays)."""
    TripLog = apps.get_model("fleet", "TripLog")
    for t in TripLog.objects.filter(webfleet_trip_id__isnull=False, notes__startswith="Webfleet: "):
        parts = t.notes[len("Webfleet: "):].split(" → ")
        if len(parts) == 2:
            t.start_place, t.end_place = parts[0][:255], parts[1][:255]
            t.save(update_fields=["start_place", "end_place"])


class Migration(migrations.Migration):

    dependencies = [
        ("fleet", "0012_webfleet_speeding"),
    ]

    operations = [
        migrations.AddField(
            model_name="triplog",
            name="start_place",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="triplog",
            name="end_place",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.RunPython(places_from_notes, migrations.RunPython.noop),
    ]
