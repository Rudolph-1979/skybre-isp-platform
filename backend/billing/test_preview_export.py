"""The Preview bundle, and the approval that gates Run.

The test that matters most is test_projection_matches_a_real_run: a preview
finance signs off is only worth anything if the run that follows produces
the same numbers, so it commits a real run and diffs the invoice it created
against the projected rows, line by line.
"""
import datetime
import io
import zipfile
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from billing.models import (
    BillingPreviewSnapshot, CustomerBillingConfig, Invoice, Service, Tariff,
)
from billing.preview_export import build_zip_from_snapshot, project_run, projection_digest
from billing.recurring import run_recurring_billing
from customers.models import Customer
from billing.gate_testing import ApprovalGateOpenMixin
from notifications.models import EmailTemplate


class PreviewProjectionTests(ApprovalGateOpenMixin, TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.tariff = Tariff.objects.create(
            name="Fibre 50/50", price=Decimal("539.00"), tax_rate_pct=Decimal("15"),
            speed_download_kbps=51200, speed_upload_kbps=51200,
        )
        self.second_tariff = Tariff.objects.create(
            name="Fibre 20/20", price=Decimal("379.00"), tax_rate_pct=Decimal("15"),
            speed_download_kbps=20480, speed_upload_kbps=20480,
        )
        EmailTemplate.objects.get_or_create(
            key=EmailTemplate.Key.INVOICE,
            defaults={"subject": "Invoice {{ invoice_number }}", "body_html": "<p>{{ balance }}</p>"},
        )

    def _customer(self, name="Hennenman Car Wash", ref="CUS-000003"):
        customer = Customer.objects.create(
            full_name=name, customer_id=ref, email="wash@example.com",
            status=Customer.Status.ACTIVE,
        )
        config = CustomerBillingConfig.objects.get_or_create(customer=customer)[0]
        config.billing_enabled = True
        config.auto_create_invoices = True
        config.send_billing_notifications = False  # keep the mailer out of these tests
        config.use_date_of_customer_creation = False
        config.billing_day = self.today.day
        config.next_billing_date = self.today
        config.save()
        return customer, config

    def _service(self, customer, tariff):
        return Service.objects.create(
            customer=customer, tariff=tariff, status=Service.Status.ACTIVE, start_date="2026-01-01",
        )

    # ---- projection ------------------------------------------------------

    def test_a_customer_with_no_active_service_is_not_projected(self):
        customer, _ = self._customer()
        self._service(customer, self.tariff).delete()
        projection = project_run(self.today)
        self.assertEqual(projection["documents"], [])
        self.assertEqual(projection["counts"]["invoices_created"], 0)

    def test_one_row_per_active_service_with_period_and_tax(self):
        customer, _ = self._customer()
        self._service(customer, self.tariff)
        self._service(customer, self.second_tariff)

        projection = project_run(self.today)
        self.assertEqual(len(projection["documents"]), 1)
        doc = projection["documents"][0]
        self.assertEqual(len(doc["lines"]), 2)
        self.assertEqual(doc["total"], Decimal("619.85") + Decimal("435.85"))
        line = doc["lines"][0]
        self.assertEqual(line["period_start"], self.today)
        self.assertEqual(line["tax_rate_pct"], Decimal("15"))

    def test_projection_matches_a_real_run(self):
        """The whole point: what was previewed is what gets billed."""
        customer, _ = self._customer()
        self._service(customer, self.tariff)
        self._service(customer, self.second_tariff)

        projection = project_run(self.today)
        projected = projection["documents"][0]

        run_recurring_billing(self.today, commit=True)
        invoice = Invoice.objects.get(customer=customer)

        self.assertEqual(invoice.total, projected["total"])
        self.assertEqual(invoice.date_due, projected["date_due"])
        self.assertEqual(
            sorted(i.description for i in invoice.items.all()),
            sorted(line["description"] for line in projected["lines"]),
        )
        self.assertEqual(
            sorted(i.unit_price for i in invoice.items.all()),
            sorted(line["unit_price"] for line in projected["lines"]),
        )

    def test_projection_counts_agree_with_the_engine(self):
        customer, _ = self._customer()
        self._service(customer, self.tariff)
        self.assertEqual(
            project_run(self.today)["counts"],
            run_recurring_billing(self.today, commit=False)["counts"],
        )

    def test_projecting_writes_nothing(self):
        customer, _ = self._customer()
        self._service(customer, self.tariff)
        project_run(self.today)
        self.assertEqual(Invoice.objects.count(), 0)
        self.assertEqual(BillingPreviewSnapshot.objects.count(), 0)

    def test_partner_scope_is_honoured(self):
        customer, _ = self._customer()
        self._service(customer, self.tariff)
        # A scope this customer is not in projects nothing at all.
        projection = project_run(self.today, partner_ids=[9999])
        self.assertEqual(projection["documents"], [])

    def test_fingerprint_changes_when_a_price_changes(self):
        customer, _ = self._customer()
        self._service(customer, self.tariff)
        before = projection_digest(project_run(self.today))
        self.tariff.price = Decimal("599.00")
        self.tariff.save(update_fields=["price"])
        self.assertNotEqual(before, projection_digest(project_run(self.today)))


class PreviewApiTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        User = get_user_model()
        self.staff = User.objects.create_user(
            username="billing-clerk", password="x", role="admin", is_staff=True, email="clerk@example.com",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.staff)
        self.tariff = Tariff.objects.create(
            name="Fibre 50/50", price=Decimal("539.00"), tax_rate_pct=Decimal("15"),
            speed_download_kbps=51200, speed_upload_kbps=51200,
        )
        EmailTemplate.objects.get_or_create(
            key=EmailTemplate.Key.INVOICE,
            defaults={"subject": "Invoice", "body_html": "<p>x</p>"},
        )
        self.customer = Customer.objects.create(
            full_name="Hennenman Car Wash", customer_id="CUS-000003",
            email="wash@example.com", status=Customer.Status.ACTIVE,
        )
        config = CustomerBillingConfig.objects.get_or_create(customer=self.customer)[0]
        config.billing_enabled = True
        config.auto_create_invoices = True
        config.send_billing_notifications = False
        config.billing_day = self.today.day
        config.next_billing_date = self.today
        config.save()
        Service.objects.create(
            customer=self.customer, tariff=self.tariff,
            status=Service.Status.ACTIVE, start_date="2026-01-01",
        )

    def _preview(self):
        return self.client.post(
            "/api/recurring-billing/preview/", {"date": self.today.isoformat()}, format="json"
        )

    def test_preview_saves_a_snapshot_and_returns_counts(self):
        response = self._preview()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["counts"]["invoices_created"], 1)
        self.assertNotIn("mismatch", response.data)
        snapshot = BillingPreviewSnapshot.objects.get()
        self.assertFalse(snapshot.is_approved)
        self.assertEqual(snapshot.generated_by, self.staff)
        self.assertEqual(Invoice.objects.count(), 0)

    def test_run_is_refused_without_an_approved_preview(self):
        self._preview()
        response = self.client.post(
            "/api/recurring-billing/run/", {"date": self.today.isoformat()}, format="json"
        )
        self.assertEqual(response.status_code, 409)
        self.assertIn("No approved preview", response.data["detail"])
        self.assertEqual(Invoice.objects.count(), 0)

    def test_approved_preview_lets_the_run_through_once(self):
        snapshot_id = self._preview().data["snapshot"]["id"]
        approve = self.client.post("/api/recurring-billing/%s/approve/" % snapshot_id)
        self.assertEqual(approve.status_code, 200)
        self.assertTrue(approve.data["is_approved"])

        run = self.client.post(
            "/api/recurring-billing/run/", {"date": self.today.isoformat()}, format="json"
        )
        self.assertEqual(run.status_code, 200)
        self.assertEqual(Invoice.objects.count(), 1)

        # The approval is spent: the same one cannot authorise a second run.
        again = self.client.post(
            "/api/recurring-billing/run/", {"date": self.today.isoformat()}, format="json"
        )
        self.assertEqual(again.status_code, 409)
        self.assertEqual(Invoice.objects.count(), 1)

    def test_an_approval_is_void_once_the_numbers_change(self):
        snapshot_id = self._preview().data["snapshot"]["id"]
        self.client.post("/api/recurring-billing/%s/approve/" % snapshot_id)

        self.tariff.price = Decimal("999.00")
        self.tariff.save(update_fields=["price"])

        response = self.client.post(
            "/api/recurring-billing/run/", {"date": self.today.isoformat()}, format="json"
        )
        self.assertEqual(response.status_code, 409)
        self.assertIn("no longer matches", response.data["detail"])
        self.assertEqual(Invoice.objects.count(), 0)

    def test_a_different_partner_scope_is_a_different_approval(self):
        snapshot_id = self._preview().data["snapshot"]["id"]
        self.client.post("/api/recurring-billing/%s/approve/" % snapshot_id)
        response = self.client.post(
            "/api/recurring-billing/run/",
            {"date": self.today.isoformat(), "partners": [4242]}, format="json",
        )
        self.assertEqual(response.status_code, 409)

    def test_export_returns_the_reviewed_bytes(self):
        snapshot_id = self._preview().data["snapshot"]["id"]
        response = self.client.get("/api/recurring-billing/%s/export/" % snapshot_id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/zip")
        self.assertIn("finance_", response["Content-Disposition"])

        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            names = archive.namelist()
            # Four sheets, the suspension-warning sheet, and the manifest.
            self.assertEqual(len(names), 6)
            self.assertTrue(any(n.startswith("warning-") for n in names), names)
            invoice_csv = next(n for n in names if n.startswith("invoice-"))
            text = archive.read(invoice_csv).decode()

        header, row = text.splitlines()[0], text.splitlines()[1]
        self.assertEqual(
            header, '"Customer id";"Customer login";"Customer name";"Customer status";Total'
        )
        self.assertIn("CUS-000003", row)
        self.assertIn("R619.85", row)  # 539.00 + 15%

    def test_export_still_serves_the_original_sheets_after_data_changes(self):
        snapshot_id = self._preview().data["snapshot"]["id"]
        self.tariff.price = Decimal("999.00")
        self.tariff.save(update_fields=["price"])
        response = self.client.get("/api/recurring-billing/%s/export/" % snapshot_id)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            invoice_csv = next(n for n in archive.namelist() if n.startswith("invoice-"))
            text = archive.read(invoice_csv).decode()
        self.assertIn("R619.85", text)
        self.assertNotIn("R1148.85", text)

    def test_pending_queue_lists_previews_for_someone_else_to_approve(self):
        self._preview()
        response = self.client.get("/api/recurring-billing/previews/?pending=1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["generated_by_name"], "billing-clerk")
        self.assertIn("download_url", response.data[0])

    def test_the_manifest_names_the_approver(self):
        snapshot_id = self._preview().data["snapshot"]["id"]
        self.client.post("/api/recurring-billing/%s/approve/" % snapshot_id)
        snapshot = BillingPreviewSnapshot.objects.get(pk=snapshot_id)
        _, blob = build_zip_from_snapshot(snapshot)
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            manifest = next(n for n in archive.namelist() if n.startswith("preview-"))
            text = archive.read(manifest).decode()
        self.assertIn("approved by     : billing-clerk", text)
        self.assertIn(snapshot.fingerprint, text)

    def test_a_bad_date_is_rejected_before_anything_is_saved(self):
        response = self.client.post(
            "/api/recurring-billing/preview/", {"date": "not-a-date"}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(BillingPreviewSnapshot.objects.count(), 0)
