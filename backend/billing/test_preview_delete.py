"""Deleting a preview that nobody approved.

The approval queue is a to-do list, and a to-do list that cannot be
cleared stops being read. Previews accumulate from wrong dates, wrong
partner scopes and re-previews after a correction, and each one sits
there looking like something waiting on a decision.

So Delete exists -- but ONLY for previews still awaiting approval. The
two refusals below are the point of this file:

  * an APPROVED preview is a decision somebody signed off. Deleting it
    would let one person undo another's approval with nothing left to
    say it happened.
  * a SPENT one is the record of a run that really invoiced customers,
    which BillingPreviewSnapshot's own docstring says is kept on purpose.

Both are 409 rather than 403: the caller is allowed to be here, the
object is in the wrong state, and the message says which.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from billing.models import (
    BillingPreviewSnapshot, CustomerBillingConfig, Invoice, Service, Tariff,
)
from customers.models import Customer
from notifications.models import EmailTemplate


class PreviewDeleteTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        User = get_user_model()
        self.staff = User.objects.create_user(
            username="billing-clerk", password="x", role="admin", is_staff=True,
            email="clerk@example.com",
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
        ).data["snapshot"]["id"]

    def _delete(self, snapshot_id):
        return self.client.delete("/api/recurring-billing/%s/" % snapshot_id)

    # --- the thing that was asked for ------------------------------------

    def test_an_unapproved_preview_can_be_deleted(self):
        snapshot_id = self._preview()

        response = self._delete(snapshot_id)

        self.assertEqual(response.status_code, 204)
        self.assertFalse(BillingPreviewSnapshot.objects.filter(pk=snapshot_id).exists())

    def test_deleting_one_leaves_the_others_alone(self):
        first = self._preview()
        second = self._preview()

        self._delete(first)

        self.assertFalse(BillingPreviewSnapshot.objects.filter(pk=first).exists())
        self.assertTrue(BillingPreviewSnapshot.objects.filter(pk=second).exists())

    def test_it_disappears_from_the_approval_queue(self):
        snapshot_id = self._preview()
        self._delete(snapshot_id)

        queue = self.client.get("/api/recurring-billing/previews/?pending=1").data

        self.assertEqual([row["id"] for row in queue], [])

    def test_deleting_a_preview_writes_no_invoices(self):
        """Obvious, and worth pinning: a preview holds a projection, not a
        run, so removing one must not touch the books in any direction."""
        snapshot_id = self._preview()

        self._delete(snapshot_id)

        self.assertEqual(Invoice.objects.count(), 0)
        self.assertEqual(self.customer.balance, Decimal("0.00"))

    # --- the refusals ----------------------------------------------------

    def test_an_approved_preview_is_refused(self):
        snapshot_id = self._preview()
        self.client.post("/api/recurring-billing/%s/approve/" % snapshot_id)

        response = self._delete(snapshot_id)

        self.assertEqual(response.status_code, 409)
        self.assertIn("approved", response.data["detail"])
        self.assertTrue(BillingPreviewSnapshot.objects.filter(pk=snapshot_id).exists())

    def test_a_spent_preview_is_refused(self):
        """The one whose approval authorised a real run. Deleting it would
        leave the run in History with nothing to say who signed it off."""
        snapshot_id = self._preview()
        self.client.post("/api/recurring-billing/%s/approve/" % snapshot_id)
        self.client.post(
            "/api/recurring-billing/run/", {"date": self.today.isoformat()}, format="json"
        )
        snapshot = BillingPreviewSnapshot.objects.get(pk=snapshot_id)
        self.assertTrue(snapshot.is_spent)

        response = self._delete(snapshot_id)

        self.assertEqual(response.status_code, 409)
        self.assertTrue(BillingPreviewSnapshot.objects.filter(pk=snapshot_id).exists())

    def test_deleting_something_that_is_not_there_is_a_404(self):
        """Two people clearing the same queue is the normal case, not an
        error worth a 500."""
        self.assertEqual(self._delete(999999).status_code, 404)

    def test_an_anonymous_caller_cannot_delete(self):
        snapshot_id = self._preview()
        self.client.force_authenticate(None)

        response = self._delete(snapshot_id)

        self.assertIn(response.status_code, (401, 403))
        self.assertTrue(BillingPreviewSnapshot.objects.filter(pk=snapshot_id).exists())
