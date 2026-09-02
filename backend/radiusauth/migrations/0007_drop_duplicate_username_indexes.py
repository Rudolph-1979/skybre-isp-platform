from django.db import migrations


class Migration(migrations.Migration):
    """Drop two redundant indexes created by 0005 and 0006.

    SessionUsageSnapshot.username is already `db_index=True` and
    RouterLiveRate.username is already `unique=True` -- both of which create
    an index on their own. The Meta.indexes entries in those migrations
    asked Postgres for a second, identical index on each table: no benefit,
    and an extra write on every sample and every poll.

    Removing them also settles the "models have changes that are not yet
    reflected in a migration" warning, which came from the hand-written
    index names not matching what Django's autodetector would generate.
    """

    dependencies = [
        ("radiusauth", "0006_routerliverate"),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name="sessionusagesnapshot",
            name="radiusauth__usernam_5a9c2f_idx",
        ),
        migrations.RemoveIndex(
            model_name="routerliverate",
            name="radiusauth__usernam_7c31d4_idx",
        ),
    ]
