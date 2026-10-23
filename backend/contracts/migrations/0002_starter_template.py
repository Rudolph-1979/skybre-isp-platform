"""A starter template: the parties, the plan table and the signing clause,
with a marked gap for Skybre's own terms. Seeded SWITCHED OFF, so it can't
be sent until somebody has put the real terms in and switched it on
(Configs -> Contracts). Automatic sending is off too."""
from django.db import migrations

STARTER_BODY = """<h3>1. The parties</h3>
<p>This agreement is between <strong>{{ company_name }}</strong>{% if company_vat_number %} (VAT {{ company_vat_number }}){% endif %}, {{ company_address }} ("we"), and
<strong>{{ client_name }}</strong>{% if client_company %} for {{ client_company }}{% endif %}{% if id_number %}, ID / registration number {{ id_number }}{% endif %}, client code {{ client_code }} ("you").</p>

<h3>2. The service</h3>
{{ services_table }}
{% if once_off_table %}<p>Once-off charges:</p>{{ once_off_table }}{% endif %}

<h3>3. Terms and conditions</h3>
<p style="color:#b91c1c;"><strong>[REPLACE THIS PARAGRAPH WITH SKYBRE'S TERMS AND CONDITIONS before switching this template on: Configs -&gt; Contracts.]</strong></p>

<h3>4. Signing</h3>
<p>You sign this agreement electronically. The signing certificate on the last page records who signed, when, and the
fingerprint of this document.</p>
"""


def seed(apps, schema_editor):
    ContractTemplate = apps.get_model("contracts", "ContractTemplate")
    ContractSettings = apps.get_model("contracts", "ContractSettings")
    template, _ = ContractTemplate.objects.get_or_create(
        name="Internet Service Agreement",
        defaults={"title": "Internet Service Agreement", "body_html": STARTER_BODY, "is_active": False},
    )
    settings_, _ = ContractSettings.objects.get_or_create(pk=1)
    if settings_.default_template_id is None:
        settings_.default_template = template
        settings_.save()


class Migration(migrations.Migration):

    dependencies = [
        ("contracts", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
