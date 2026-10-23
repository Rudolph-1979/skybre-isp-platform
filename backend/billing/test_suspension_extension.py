"""Pausing suspension AND the reminder/warning chasers for one customer,
by Management-approved request -- not a permanent per-customer setting.

Rudolph, 25 Sep: "Customer Payment suspension extension option." Checked
in source first: the only existing lever was blocking_period_days, a
permanent per-customer template field -- nothing let staff say "don't cut
this one off, just this once, they promised to pay by Friday" without
changing their standing terms.

His three answers shaped this:
  1. An extension pauses BOTH suspension and the reminder/warning chasers
     (not suspension alone).
  2. Needs Management approval -- same trust tier as CreditRequest and
     InvoiceDeletionRequest: staff requests, Management decides.
  3. Lives as a field on the customer's own Billing tab.

Three places in billing/recurring.py all have to honour it or the
platform contradicts itself in front of a customer, same principle
test_business_days.py already states for blocking_candidate_services /
suspension_block_date / upcoming_blocks:

  * blocking_candidate_services -- the real suspension pass;
  * suspension_block_date       -- what the warning email would promise
                                    (and, transitively, upcoming_blocks);
  * _process_reminders          -- the payment-reminder chaser.
"""
import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.testing import ALL_SECTIONS
from customers.models import Customer

from .models import CustomerBillingConfig, Invoice, ReminderSettings, Service, SuspensionExtensionRequest, Tariff
from .recurring import _process_reminders, blocking_candidate_services, suspension_block_date

User = get_user_model()

TODAY = datetime.date(2026, 9, 25)  # a Friday
EXTENDED_UNTIL = datetime.date(2026, 9, 30)  # a Wednesday -- both this and
AFTER_EXTENSION = datetime.date(2026, 10, 1)  # the next day are business
# days, so is_business_day doesn't confound what these tests are about.


class SuspensionExtensionPredicateTests(TestCase):
    """blocking_candidate_services and suspension_block_date, direct."""

    def setUp(self):
        self.tariff = Tariff.objects.create(
            name="Home 20", price=Decimal("500.00"), tax_rate_pct=Decimal("15.00"),
        )
        self.customer = Customer.objects.create(
            full_name="Christo Van Den Heever", email="christo@example.com",
            status=Customer.Status.ACTIVE, balance=Decimal("575.00"),
        )
        Service.objects.create(
            customer=self.customer, tariff=self.tariff, status=Service.Status.ACTIVE,
            start_date=TODAY - datetime.timedelta(days=90), radius_username="christo",
        )
        self.config = CustomerBillingConfig.for_customer(self.customer)
        self.config.billing_enabled = True
        self.config.auto_create_invoices = False
        self.config.blocking_period_days = 0
        self.config.reminder_enabled = True
        self.config.reminder_1_day = -3
        self.config.save()
        Invoice.objects.filter(
            pk=Invoice.objects.create(
                customer=self.customer, status=Invoice.Status.UNPAID, date_due=TODAY,
            ).pk
        ).update(
            total=Decimal("575.00"),
            # Already reminded, long enough ago that the "no reminder, no
            # block" rule (12 Oct 2026) is satisfied -- these tests are about
            # the extension, not the reminder.
            last_reminder_run_date=TODAY - datetime.timedelta(days=30),
            # Issued on its due date, as in life: the countdown runs from when
            # the balance went over the minimum (21 Oct 2026).
            date_issued=TODAY,
        )

    def test_would_be_blocked_with_no_extension(self):
        self.assertNotEqual(blocking_candidate_services(self.customer, self.config, TODAY), [])
        self.assertIsNotNone(suspension_block_date(self.customer, self.config, TODAY))

    def test_extension_stops_blocking_before_its_date(self):
        """Protected BEFORE the agreed date; ON it the line goes off (20 Oct
        2026, Rudolph: "chosen extension till date should suspend client on
        selected date")."""
        self.config.suspension_extended_until = EXTENDED_UNTIL
        self.config.save()
        before = EXTENDED_UNTIL - datetime.timedelta(days=1)

        self.assertEqual(blocking_candidate_services(self.customer, self.config, before), [])
        self.assertIsNone(suspension_block_date(self.customer, self.config, before))
        self.assertNotEqual(blocking_candidate_services(self.customer, self.config, EXTENDED_UNTIL), [])
        self.assertEqual(suspension_block_date(self.customer, self.config, EXTENDED_UNTIL), EXTENDED_UNTIL)

    def test_blocking_resumes_the_day_after_the_extension(self):
        """Nothing is forgiven, only deferred -- the same principle
        test_business_days.py states for a weekend/holiday deferral. The
        debt is unchanged, so the day after the extension the customer is
        exactly as overdue as before, and qualifies again with no expiry
        job needed."""
        self.config.suspension_extended_until = EXTENDED_UNTIL
        self.config.save()

        self.assertNotEqual(blocking_candidate_services(self.customer, self.config, AFTER_EXTENSION), [])
        self.assertIsNotNone(suspension_block_date(self.customer, self.config, AFTER_EXTENSION))

    def test_a_past_extension_date_has_no_effect(self):
        self.config.suspension_extended_until = TODAY - datetime.timedelta(days=1)
        self.config.save()

        self.assertNotEqual(blocking_candidate_services(self.customer, self.config, TODAY), [])


class SuspensionExtensionRemindersTests(TestCase):
    """_process_reminders, direct -- the chaser email, not only the cut-off."""

    def setUp(self):
        self.tariff = Tariff.objects.create(
            name="Home 20", price=Decimal("500.00"), tax_rate_pct=Decimal("15.00"),
        )
        self.customer = Customer.objects.create(
            full_name="Christo Van Den Heever", email="christo@example.com",
            status=Customer.Status.ACTIVE, balance=Decimal("575.00"),
        )
        self.config = CustomerBillingConfig.for_customer(self.customer)
        self.config.billing_enabled = True
        self.config.reminder_enabled = True
        self.config.reminder_1_day = 0
        self.config.save()
        self.due_today = Invoice.objects.create(
            customer=self.customer, status=Invoice.Status.UNPAID, date_due=TODAY,
        )
        Invoice.objects.filter(pk=self.due_today.pk).update(total=Decimal("575.00"))
        self.reminder_settings = ReminderSettings.load()

    def test_a_reminder_would_normally_be_sent(self):
        count = _process_reminders(self.customer, self.config, self.reminder_settings, TODAY, commit=False)
        self.assertEqual(count, 1)

    def test_extension_pauses_the_reminder_too(self):
        self.config.suspension_extended_until = EXTENDED_UNTIL
        self.config.save()

        count = _process_reminders(self.customer, self.config, self.reminder_settings, TODAY, commit=False)
        self.assertEqual(count, 0)


class SuspensionExtensionRequestApiTests(TestCase):
    """The request/approve/reject workflow -- staff requests, Management
    decides, same shape as CreditRequest and InvoiceDeletionRequest."""

    def setUp(self):
        self.client = APIClient()
        self.staff = User.objects.create_user(
            username="finance1", password="pw-for-tests", role=User.Role.ACCOUNTS,
            allowed_sections=ALL_SECTIONS,
        )
        self.manager = User.objects.create_user(
            username="manager1", password="pw-for-tests", role=User.Role.MANAGEMENT,
            allowed_sections=ALL_SECTIONS,
        )
        self.customer = Customer.objects.create(
            full_name="Christo Van Den Heever", email="christo@example.com",
            status=Customer.Status.ACTIVE,
        )

    def _request(self, extend_until=EXTENDED_UNTIL, reason="Promised to pay by Friday"):
        return self.client.post(
            "/api/suspension-extension-requests/",
            {"customer": self.customer.id, "extend_until": str(extend_until), "reason": reason},
            format="json",
        )

    def test_staff_can_submit_a_request(self):
        self.client.force_authenticate(self.staff)
        res = self._request()
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["status"], "pending")

    def test_a_reason_is_required(self):
        self.client.force_authenticate(self.staff)
        res = self._request(reason="")
        self.assertEqual(res.status_code, 400)

    def test_cannot_extend_into_the_past(self):
        self.client.force_authenticate(self.staff)
        res = self._request(extend_until=TODAY - datetime.timedelta(days=365))
        self.assertEqual(res.status_code, 400)

    def test_a_second_pending_request_is_refused(self):
        self.client.force_authenticate(self.staff)
        self._request()
        res = self._request()
        self.assertEqual(res.status_code, 400)

    def test_staff_cannot_approve_their_own_request(self):
        self.client.force_authenticate(self.staff)
        created = self._request()
        res = self.client.post(f"/api/suspension-extension-requests/{created.data['id']}/approve/")
        self.assertEqual(res.status_code, 403)

    def test_management_approving_sets_the_customer_s_config(self):
        self.client.force_authenticate(self.staff)
        created = self._request()

        self.client.force_authenticate(self.manager)
        res = self.client.post(f"/api/suspension-extension-requests/{created.data['id']}/approve/")
        self.assertEqual(res.status_code, 200, res.data)

        config = CustomerBillingConfig.for_customer(self.customer)
        self.assertEqual(config.suspension_extended_until, EXTENDED_UNTIL)

        ext = SuspensionExtensionRequest.objects.get(pk=created.data["id"])
        self.assertEqual(ext.status, "approved")
        self.assertEqual(ext.decided_by, self.manager)

    def test_rejecting_leaves_the_config_untouched(self):
        self.client.force_authenticate(self.staff)
        created = self._request()

        self.client.force_authenticate(self.manager)
        res = self.client.post(
            f"/api/suspension-extension-requests/{created.data['id']}/reject/",
            {"decision_note": "Already on a payment plan"},
            format="json",
        )
        self.assertEqual(res.status_code, 200, res.data)

        config = CustomerBillingConfig.for_customer(self.customer)
        self.assertIsNone(config.suspension_extended_until)

    def test_a_decided_request_cannot_be_approved_twice(self):
        self.client.force_authenticate(self.staff)
        created = self._request()

        self.client.force_authenticate(self.manager)
        first = self.client.post(f"/api/suspension-extension-requests/{created.data['id']}/approve/")
        self.assertEqual(first.status_code, 200)
        second = self.client.post(f"/api/suspension-extension-requests/{created.data['id']}/approve/")
        self.assertEqual(second.status_code, 400)

    def test_requester_can_withdraw_their_own_pending_request(self):
        self.client.force_authenticate(self.staff)
        created = self._request()
        res = self.client.delete(f"/api/suspension-extension-requests/{created.data['id']}/")
        self.assertEqual(res.status_code, 204)
        self.assertFalse(SuspensionExtensionRequest.objects.filter(pk=created.data["id"]).exists())

    def test_a_decided_request_cannot_be_withdrawn_by_the_requester(self):
        self.client.force_authenticate(self.staff)
        created = self._request()
        self.client.force_authenticate(self.manager)
        self.client.post(f"/api/suspension-extension-requests/{created.data['id']}/approve/")

        self.client.force_authenticate(self.staff)
        res = self.client.delete(f"/api/suspension-extension-requests/{created.data['id']}/")
        self.assertEqual(res.status_code, 405)
