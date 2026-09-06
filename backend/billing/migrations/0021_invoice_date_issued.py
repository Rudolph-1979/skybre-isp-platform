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

    Except a cancelled QUOTE. Cancelling is the documented end state of a
    quote that came to nothing -- InvoiceSerializer.validate_status tells
    staff exactly that -- and `status` alone cannot say which kind of
    document a cancelled row was. The NUMBER can: a quote/pro forma keeps
    its QUO-/PF- number right up until convert_to_invoice() reissues it out
    of the INV sequence, so a cancelled QUO-/PF- row was never a tax
    invoice. Stamping one anyway matters the day somebody revives it:
    save() will not re-stamp a date that is already there, so the revived
    document's VAT is declared in the month the quote was raised -- a
    period whose VAT201 was filed long ago -- which is the exact gap this
    field exists to close.
    """
    Invoice = apps.get_model("billing", "Invoice")
    Invoice.objects.filter(date_issued__isnull=True).exclude(
        status__in=["quote", "proforma", "draft"]
    ).exclude(
        models.Q(status="cancelled")
        & (models.Q(number__startswith="QUO-") | models.Q(number__startswith="PF-"))
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
