from django.db import migrations

# Default content for the 5 fixed template "slots". Kept here (rather than
# only inline in this migration) isn't necessary since nothing else needs to
# reference these strings, but they're intentionally simple, safe HTML so a
# fresh install always has sensible working templates staff can then edit
# from the Email Templates admin page.
DEFAULTS = [
    {
        "key": "welcome",
        "name": "Welcome message",
        "subject": "Welcome to {{ company_name }}, {{ customer_name }}!",
        "has_attachment": False,
        "body_html": (
            "<p>Hi {{ customer_name }},</p>"
            "<p>Welcome to {{ company_name }}! We're delighted to have you on board as customer "
            "<strong>{{ customer_id }}</strong>.</p>"
            "<p>You can view your invoices and log support requests any time from the customer portal:</p>"
            '<p><a href="{{ portal_url }}">{{ portal_url }}</a></p>'
            "<p>If you have any questions, just reply to this email.</p>"
            "<p>Thanks,<br>The {{ company_name }} Team</p>"
        ),
    },
    {
        "key": "statement",
        "name": "Statement",
        "subject": "Your {{ company_name }} account statement",
        "has_attachment": True,
        "body_html": (
            "<p>Hi {{ customer_name }},</p>"
            "<p>Please find attached your account statement as at {{ statement_date }}.</p>"
            "<p>Current outstanding balance: <strong>R {{ balance }}</strong></p>"
            "<p>If you've already paid, please disregard this message.</p>"
            "<p>Thanks,<br>The {{ company_name }} Team</p>"
        ),
    },
    {
        "key": "invoice",
        "name": "Invoice",
        "subject": "Invoice {{ invoice_number }} from {{ company_name }}",
        "has_attachment": True,
        "body_html": (
            "<p>Hi {{ customer_name }},</p>"
            "<p>Please find attached invoice <strong>{{ invoice_number }}</strong> for "
            "<strong>R {{ invoice_total }}</strong>, due on {{ invoice_due_date }}.</p>"
            "<p>Your current account balance is R {{ balance }}.</p>"
            "<p>Thanks,<br>The {{ company_name }} Team</p>"
        ),
    },
    {
        "key": "payment_reminder",
        "name": "Payment reminder",
        "subject": "Payment reminder — your {{ company_name }} account",
        "has_attachment": False,
        "body_html": (
            "<p>Hi {{ customer_name }},</p>"
            "<p>This is a friendly reminder that your account currently has an outstanding balance of "
            "<strong>R {{ balance }}</strong>{% if invoice_number %} (invoice {{ invoice_number }}, "
            "due {{ invoice_due_date }}){% endif %}.</p>"
            "<p>Please arrange payment as soon as possible to avoid any interruption to your service.</p>"
            "<p>Thanks,<br>The {{ company_name }} Team</p>"
        ),
    },
    {
        "key": "suspension",
        "name": "Suspension notification",
        "subject": "Your {{ company_name }} service has been suspended",
        "has_attachment": False,
        "body_html": (
            "<p>Hi {{ customer_name }},</p>"
            "<p>We're writing to let you know that your {{ company_name }} service has been suspended due to "
            "an outstanding balance of <strong>R {{ balance }}</strong>.</p>"
            "<p>To restore your service, please settle your account as soon as possible. If you've recently "
            "made a payment, please disregard this notice.</p>"
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
        ("notifications", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_templates, remove_templates),
    ]
