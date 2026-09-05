from django.db import migrations, models


def backfill_date_issued(apps, schema_editor):
    """Give every document that is already a tax invoice an issue date.

    date_created is the best evidence available for invoices raised before
    this field existed -- for anything created directly as an invoice it is
    exactly right, and for a converted quote it is the only date on the row.
    That is the same (wrong, for converted quotes) basis the VAT return was
    already using, so the backfill changes no historical return; it just
    stops the gap from here on.

    Quotes, pro formas and drafts are deliberately left NULL: they are not
    tax invoices yet, and when they are converted, Invoice.save() stamps the
    day that actually happens, which is the whole point of the field.
    Cancelled documents are backfilled too -- a cancelled invoice was issued
    at some point, and leaving it NULL would let a later un-cancel stamp it
    with today's date.
    """
    Invoice = apps.get_model("billing", "Invoice")
    Invoice.objects.filter(date_issued__isnull=True).exclude(
        status__in=["quote", "proforma", "draft"]
    ).update(date_issued=models.F("date_created"))


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0020_alter_invoice_balance_debited"),
    ]

    operations = [
        migrations.AddField(
            model_name="invoice",
            name="date_issued",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.RunPython(backfill_date_issued, migrations.RunPython.noop),
    ]
