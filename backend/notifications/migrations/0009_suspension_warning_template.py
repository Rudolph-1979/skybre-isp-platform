"""The email that warns a customer BEFORE their line goes off.

Until now the sequence a customer experienced was: a payment reminder
saying "to avoid any interruption to your service", then silence for
however many days their blocking period runs, then a notice telling them
the line had already been suspended. Nothing named a date, and nothing
said what it would take to stop it.

Seeded with get_or_create, following 0003 and 0007: if somebody has
already written this template by hand, theirs is left alone. The default
body is deliberately close to the suspension notice's -- same reference,
same banking details, same page to pay on -- because the one moment the
customer needs those is BEFORE the cut-off, not after it.
"""
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
]

DEFAULTS = [
    {
        "key": "suspension_warning",
        "name": "Suspension warning (before cut-off)",
        "subject": "Action needed — your {{ company_name }} line will be suspended on {{ suspend_date }}",
        "has_attachment": False,
        "body_html": (
            "<p>Hi {{ customer_name }},</p>"
            "<p>Your account has an outstanding balance of <strong>R {{ balance }}</strong>. "
            "If it is not settled, your internet service will be suspended on "
            "<strong>{{ suspend_date }}</strong> — that is {{ days_until_suspension }} day(s) "
            "from today.</p>"
            "<p>Please use this reference on your payment, or we will not be able to match it "
            "to your account:</p>"
            "<p style=\"font-size:18px;font-weight:bold;color:#ff0000;margin:4px 0 14px 0;\">"
            "{{ customer_id }}</p>"
            "{% if bank_account_number %}<p><strong>Where to pay</strong><br>"
            "{% if bank_name %}Bank: {{ bank_name }}<br>{% endif %}"
            "Account number: {{ bank_account_number }}"
            "{% if bank_branch_code %}<br>Branch code: {{ bank_branch_code }}{% endif %}</p>"
            "{% endif %}"
            "<p>Once you have paid, send us the proof of payment and nothing further will "
            "happen to your line. If you have already paid, please disregard this notice.</p>"
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
        ("notifications", "0008_emaillog_staff_alter_emaillog_template_key_and_more"),
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
