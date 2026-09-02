"""NetworkSite.partner (one) -> NetworkSite.partners (many).

The generated version of this migration removed the old column BEFORE adding
the new one, which would have thrown away every site's existing partner in
silence. The order here is deliberate: add, copy, then remove -- and the copy
runs in both directions, so a rollback keeps the data too.

A site whose partners set is empty means "all partners", the same convention
as Device.visible_partners and User.allowed_partners. That is exactly what a
site with no partner meant before, so nothing needs translating for those.
"""
from django.db import migrations, models


def copy_partner_to_partners(apps, schema_editor):
    NetworkSite = apps.get_model("network", "NetworkSite")
    for site in NetworkSite.objects.exclude(partner__isnull=True).iterator():
        site.partners.add(site.partner_id)


def copy_partners_to_partner(apps, schema_editor):
    """Rolling back keeps the FIRST partner, since the column can only hold
    one. Lossy by nature -- a site serving three partners cannot go back to a
    single column without dropping two -- but losing two is better than
    losing all three."""
    NetworkSite = apps.get_model("network", "NetworkSite")
    for site in NetworkSite.objects.iterator():
        first = site.partners.order_by("pk").first()
        if first is not None:
            site.partner_id = first.pk
            site.save(update_fields=["partner"])


class Migration(migrations.Migration):

    dependencies = [
        ("customers", "0012_customer_live_bandwidth_last_viewed_at"),
        ("network", "0007_alter_ippool_category"),
    ]

    operations = [
        migrations.AddField(
            model_name="networksite",
            name="partners",
            field=models.ManyToManyField(
                blank=True,
                help_text="Partners served from this site. Leave empty for all.",
                related_name="network_sites",
                to="customers.partner",
            ),
        ),
        migrations.RunPython(copy_partner_to_partners, copy_partners_to_partner),
        migrations.RemoveField(
            model_name="networksite",
            name="partner",
        ),
    ]
