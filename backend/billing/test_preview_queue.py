"""The approval queue as the Finance page reads it back.

Run is unlocked by an approved, unspent preview. The page used to know
about that approval only from the Preview call it made itself, so the
reviewer who approved somebody else's preview -- the entire reason the
queue exists -- was left with Run greyed out, and so was anyone who
reloaded the page after approving. No invoices were raised on the 16th
of September 2026 for exactly that reason: previewed, approved, and the
button never became clickable.

The fix reads the approval back from ?date=, so these pin the contract
that fix depends on. They are API tests, not UI tests, but this is the
part that would break silently: a filter change here turns Run back into
a button that cannot be armed, and nothing else would notice.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from billing.models import CustomerBillingConfig, Service, Tariff
from customers.models import Customer, Partner
from notifications.models import EmailTemplate


class PreviewQueueTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        User = get_user_model()
        self.generator = User.objects.create_user(
            username="clerk", password="x", role="admin", is_staff=True, email="c@example.com",
        )
        self.reviewer = User.objects.create_user(
            username="reviewer", password="x", role="admin", is_staff=True, email="r@example.com",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.generator)

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

    def _preview(self, partners=None):
        return self.client.post(
            "/api/recurring-billing/preview/",
            {"date": self.today.isoformat(), "partners": partners or []},
            format="json",
        ).data["snapshot"]

    def _for_date(self):
        return self.client.get(
            "/api/recurring-billing/previews/?date=%s" % self.today.isoformat()
        ).data

    # --- what arms Run ---------------------------------------------------

    def test_an_approved_preview_is_still_findable_by_date(self):
        """?pending=1 hides it the moment it is approved, which is right for
        the queue and useless for arming Run. ?date= must still return it."""
        snapshot_id = self._preview()["id"]
        self.client.post("/api/recurring-billing/%s/approve/" % snapshot_id)

        rows = self._for_date()

        match = [r for r in rows if r["id"] == snapshot_id]
        self.assertEqual(len(match), 1)
        self.assertTrue(match[0]["is_approved"])
        self.assertFalse(match[0]["is_spent"])

    def test_a_second_person_sees_the_approval_they_did_not_make(self):
        """The hand-off: one person previews, another approves and runs. The
        approval has to be visible to a browser that never saw the preview."""
        snapshot_id = self._preview()["id"]
        self.client.force_authenticate(self.reviewer)
        self.client.post("/api/recurring-billing/%s/approve/" % snapshot_id)

        rows = self._for_date()

        armed = [r for r in rows if r["is_approved"] and not r["is_spent"]]
        self.assertEqual([r["id"] for r in armed], [snapshot_id])
        self.assertEqual(armed[0]["approved_by_name"], "reviewer")

    def test_the_partner_scope_is_returned_so_the_page_can_match_on_it(self):
        """An approval for one partner scope must not arm Run for another --
        the server would refuse it, after the operator had pressed the
        button believing it was ready."""
        partner = Partner.objects.create(name="MTD")
        scoped_id = self._preview(partners=[partner.pk])["id"]
        self.client.post("/api/recurring-billing/%s/approve/" % scoped_id)

        row = [r for r in self._for_date() if r["id"] == scoped_id][0]

        self.assertEqual(row["partner_ids"], [partner.pk])

    def test_a_spent_approval_stops_arming_anything(self):
        """Once it has been used for a run it must not re-arm Run on the next
        page load -- that is a second billing cycle one click away."""
        snapshot_id = self._preview()["id"]
        self.client.post("/api/recurring-billing/%s/approve/" % snapshot_id)
        self.client.post(
            "/api/recurring-billing/run/", {"date": self.today.isoformat()}, format="json"
        )

        row = [r for r in self._for_date() if r["id"] == snapshot_id][0]

        self.assertTrue(row["is_spent"])
        self.assertIsNotNone(row["run"])

    def test_a_date_with_nothing_approved_returns_nothing_to_arm_from(self):
        self._preview()  # generated, never approved

        rows = self._for_date()

        self.assertEqual([r for r in rows if r["is_approved"]], [])
