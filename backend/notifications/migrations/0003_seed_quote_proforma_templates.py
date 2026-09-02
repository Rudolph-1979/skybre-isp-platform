from django.db import migrations

# Default content for the 2 new template "slots" added alongside the
# Quote -> Pro Forma -> Invoice feature. Mirrors the pattern in
# 0002_seed_email_templates.py.
DEFAULTS = [
    {
        "key": "quote",
        "name": "Quote",
        "subject": "Quotation {{ invoice_number }} from {{ company_name }}",
        "has_attachment": True,
        "body_html": (
            "<p>Hi {{ customer_name }},</p>"
            "<p>Please find attached quotation <strong>{{ invoice_number }}</strong> for "
            "<strong>R {{ invoice_total }}</strong>, valid until {{ invoice_due_date }}.</p>"
            "<p>Let us know if you'd like to go ahead and we'll get it actioned.</p>"
            "<p>Thanks,<br>The {{ company_name }} Team</p>"
        ),
    },
    {
        "key": "proforma",
        "name": "Pro forma invoice",
        "subject": "Pro forma invoice {{ invoice_number }} from {{ company_name }}",
        "has_attachment": True,
        "body_html": (
            "<p>Hi {{ customer_name }},</p>"
            "<p>Please find attached pro forma invoice <strong>{{ invoice_number }}</strong> for "
            "<strong>R {{ invoice_total }}</strong>.</p>"
            "<p>This is not a tax invoice or a demand for payment — please use it to arrange payment "
            "or internal approval, and we'll issue the official tax invoice once that's done.</p>"
            "<p>Thanks,<br>The {{ company_name }} Team</p>"
        ),
    },
]


def seed_templates(apps, schema_editor):
    EmailTemplate = apps.get_model("notifications", "EmailTemplate")
    for d in DEFAULTS:
        EmailTemplate.objects.get_or_create(key=d["key"], defaults=d)


def remove_templates(apps, schema_editor):
    EmailTemplate = apps.get_model("notifications", "EmailTemplate")
    EmailTemplate.objects.filter(key__in=[d["key"] for d in DEFAULTS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0002_seed_email_templates"),
    ]

    operations = [
        migrations.RunPython(seed_templates, remove_templates),
    ]
