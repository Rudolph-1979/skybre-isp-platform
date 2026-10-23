"""The two contract emails (6 Oct 2026): the signing link, and the signed
copy. Seeded with get_or_create like 0009, so a hand-written one is kept."""
from django.db import migrations, models

CHOICES = [
    ("welcome", "Welcome message"),
    ("quote", "Quote"),
    ("proforma", "Pro forma invoice"),
    ("statement", "Statement"),
    ("invoice", "Invoice"),
    ("payment_reminder", "Payment reminder"),
    ("suspension_warning", "Suspension warning (before cut-off)"),
    ("suspension", "Suspension notification"),
    ("payment_received", "Payment received"),
    ("payslip", "Payslip"),
    ("contract", "Contract for signing"),
    ("contract_signed", "Signed contract (copy)"),
]

DEFAULTS = [
    {
        "key": "contract",
        "name": "Contract for signing",
        "subject": "Please sign: {{ contract_title }} — {{ company_name }}",
        "has_attachment": False,
        "body_html": (
            "<p><img src=\"{{ site_url }}/apple-touch-icon.png\" alt=\"{{ company_name }}\" "
            "width=\"56\" height=\"56\"></p>"
            "<p>Hi {{ customer_name }},</p>"
            "<p>Your <strong>{{ contract_title }}</strong> ({{ contract_reference }}) is ready to sign. "
            "Open it on your phone or computer, read it, and sign with your finger or mouse:</p>"
            "<p><a href=\"{{ signing_url }}\" style=\"display:inline-block;padding:10px 18px;"
            "background:#1d4ed8;color:#ffffff;text-decoration:none;border-radius:6px;\">"
            "Read and sign</a></p>"
            "<p>The link works until {{ link_expires }}. Once you have signed, we will email you a copy.</p>"
            "<p>If you have any questions, reply to this email before signing.</p>"
            "<p>Thanks,<br>The {{ company_name }} Team</p>"
        ),
    },
    {
        "key": "contract_signed",
        "name": "Signed contract (copy)",
        "subject": "Your signed {{ contract_title }} — {{ company_name }}",
        "has_attachment": True,
        "body_html": (
            "<p><img src=\"{{ site_url }}/apple-touch-icon.png\" alt=\"{{ company_name }}\" "
            "width=\"56\" height=\"56\"></p>"
            "<p>Hi {{ customer_name }},</p>"
            "<p>Thank you for signing your <strong>{{ contract_title }}</strong> ({{ contract_reference }}). "
            "Your signed copy is attached — please keep it for your records.</p>"
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
        ("notifications", "0010_emaillog_cc_email"),
    ]

    operations = [
        migrations.AlterField(
            model_name="emailtemplate",
            name="key",
            field=models.CharField(choices=CHOICES, max_length=30, unique=True),
        ),
        migrations.AlterField(
            model_name="emaillog",
            name="template_key",
            field=models.CharField(choices=CHOICES, max_length=30),
        ),
        migrations.RunPython(seed_templates, remove_templates),
    ]
