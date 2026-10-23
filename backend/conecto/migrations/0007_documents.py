"""Invoice pushes, and revocable per-document URLs for the Conecto app.

Hand-written like the rest: makemigrations inside the container writes to
the container's writable layer and the next build discards it.

Two tables.

ConectoInvoice is the usual push link -- what we last sent Conecto about
an invoice, so an unrelated save does not re-push it and a cancellation
after a push is detectable.

ConectoDocumentToken is the answer to a problem Conecto spotted in our
first proposal: a signed URL captured at push time and tapped weeks later
is a dead link by construction, because the expiry and the moment of use
have nothing to do with each other. So the URL is minted on demand, and
the token is a stored random UUID rather than a signature -- the same
choice, for the same reason, as Customer.usage_token: a signature cannot
be revoked, and these open a customer's tax invoice.
"""
import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("conecto", "0006_bandwidth_session"),
        ("billing", "0001_initial"),
        ("customers", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="ConectoInvoice",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("pushed_number", models.CharField(max_length=30)),
                ("pushed_status", models.CharField(blank=True, max_length=20)),
                ("pushed_digest", models.CharField(blank=True, max_length=32)),
                ("pushed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "invoice",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="conecto",
                        to="billing.invoice",
                    ),
                ),
            ],
            options={"ordering": ["-pushed_at"]},
        ),
        migrations.CreateModel(
            name="ConectoDocumentToken",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("token", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                # choices included so the model and this migration agree.
                # They were missing, and `makemigrations --check` correctly
                # reported drift -- which mattered less for what it was
                # (choices never touch the database, so the schema was always
                # right) than for what it disabled: that check is the guard
                # against a REAL mismatch, and a guard that always fails is
                # a guard nobody reads.
                #
                # Edited in place rather than corrected by an 0008. This
                # migration is already applied, and normally that would make
                # editing it wrong -- but a choices-only change alters no
                # column, so replaying it on a fresh database produces
                # exactly the schema production already has.
                (
                    "kind",
                    models.CharField(
                        choices=[("invoice", "Invoice"), ("statement", "Statement")],
                        max_length=20,
                    ),
                ),
                ("period", models.CharField(blank=True, max_length=7)),
                ("expires_at", models.DateTimeField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("fetched_at", models.DateTimeField(blank=True, null=True)),
                (
                    "customer",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="conecto_document_tokens",
                        to="customers.customer",
                    ),
                ),
                (
                    "invoice",
                    models.ForeignKey(
                        blank=True, null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="conecto_document_tokens",
                        to="billing.invoice",
                    ),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex(
            model_name="conectodocumenttoken",
            index=models.Index(fields=["expires_at"], name="conecto_doctoken_expiry_idx"),
        ),
    ]
