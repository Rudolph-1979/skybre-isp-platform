"""Guards on a committed recurring-billing run.

Two defects, both about a run doing more than the person who started it
asked for.

THE RUN LOCK. At 1,592 customers a run takes minutes -- five queries, one
SMTP connection and one PDF render each -- and nothing stopped a second
Run click. The only guard against re-invoicing is next_billing_date, and
the old code read it from a queryset snapshot materialised before the
first invoice was written. So an overlapping run saw every date still in
the past and raised a second invoice, number, balance debit and emailed
PDF for every due customer, then wrote the same next_billing_date and
left no trace. apply_speed_policies has taken an advisory lock all along.

THE RUN DATE. A committed run calls apply_due_tariff_changes and
apply_due_cancellations, both of which use `<=` deliberately so a job
that missed a day catches up. Handed a future date that catch-up becomes
a fast-forward: every service with an end date up to then is TERMINATED
and every booked tariff change lands, months early. `--date` is
documented as a way to "preview a specific date".
"""
import datetime
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from billing.models import (
    CustomerBillingConfig, Invoice, RecurringBillingRun, Service, Tariff,
)
from billing.recurring import (
    RecurringBillingBusy, _release_run_lock, _try_run_lock, run_recurring_billing,
)
from customers.models import Customer

User = get_user_model()


class _RunTestCase(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.tariff = Tariff.objects.create(
            name="Home 20", price=Decimal("500.00"), tax_rate_pct=Decimal("0.00"),
            speed_download_kbps=20480, speed_upload_kbps=10240,
        )

    def _billable(self, name, due=None):
        customer = Customer.objects.create(
            full_name=name, email=f"{name.split()[0].lower()}@example.com",
            status=Customer.Status.ACTIVE, balance=Decimal("0.00"),
        )
        Service.objects.create(
            customer=customer, tariff=self.tariff, status=Service.Status.ACTIVE,
            start_date=self.today - datetime.timedelta(days=90),
        )
        config = CustomerBillingConfig.for_customer(customer)
        config.billing_enabled = True
        config.next_billing_date = due or self.today
        config.save()
        return customer


class RunLockTests(_RunTestCase):
    def test_a_run_refuses_to_start_while_another_holds_the_lock(self):
        self._billable("Locked Lerato")
        with mock.patch("billing.recurring._try_run_lock", return_value=False):
            with self.assertRaises(RecurringBillingBusy):
                run_recurring_billing(self.today, commit=True)
        # And crucially, nothing was written.
        self.assertEqual(Invoice.objects.count(), 0)
        self.assertEqual(RecurringBillingRun.objects.count(), 0)

    def test_the_lock_is_released_after_a_successful_run(self):
        self._billable("Released Rina")
        with mock.patch("billing.recurring._release_run_lock") as release:
            run_recurring_billing(self.today, commit=True)
        release.assert_called_once()

    def test_the_lock_is_released_even_when_the_run_raises(self):
        """Otherwise one crashed run blocks every future run until the
        connection is recycled."""
        self._billable("Crashing Cara")
        with mock.patch("billing.recurring._release_run_lock") as release, \
             mock.patch("billing.recurring._run_commit", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                run_recurring_billing(self.today, commit=True)
        release.assert_called_once()

    def test_the_lock_helpers_actually_take_a_postgres_lock(self):
        """The mock-based tests above prove the behaviour; this proves the
        mechanism, from a second connection, because a Postgres advisory
        lock is re-entrant within one session and would otherwise appear
        to work while protecting nothing."""
        import psycopg2

        from django.db import connection

        params = connection.get_connection_params()
        params.pop("cursor_factory", None)
        params.pop("context", None)
        self.assertTrue(_try_run_lock())
        try:
            other = psycopg2.connect(**params)
            try:
                with other.cursor() as cursor:
                    cursor.execute("SELECT pg_try_advisory_lock(%s, %s)", [0x5B17, 1])
                    self.assertFalse(cursor.fetchone()[0])
            finally:
                other.close()
        finally:
            _release_run_lock()

    def test_a_preview_takes_no_lock_and_can_run_alongside_a_run(self):
        self._billable("Preview Pia")
        with mock.patch("billing.recurring._try_run_lock") as try_lock:
            result = run_recurring_billing(self.today, commit=False)
        try_lock.assert_not_called()
        self.assertEqual(result["counts"]["invoices_created"], 1)
        self.assertEqual(Invoice.objects.count(), 0)


class RunDateTests(_RunTestCase):
    def test_a_committed_run_refuses_a_future_date(self):
        self._billable("Future Fikile")
        future = self.today + datetime.timedelta(days=90)
        with self.assertRaises(ValueError) as caught:
            run_recurring_billing(future, commit=True)
        self.assertIn("in the future", str(caught.exception))
        self.assertEqual(Invoice.objects.count(), 0)

    def test_a_future_committed_run_does_not_terminate_scheduled_services(self):
        """The damaging half: apply_due_cancellations uses `<=`, so a
        future run date executes months of scheduled cancellations now."""
        customer = self._billable("Ending Enzo")
        service = customer.services.get()
        service.end_date = self.today + datetime.timedelta(days=60)
        service.save()

        with self.assertRaises(ValueError):
            run_recurring_billing(self.today + datetime.timedelta(days=90), commit=True)
        service.refresh_from_db()
        self.assertEqual(service.status, Service.Status.ACTIVE)

    def test_today_is_allowed(self):
        self._billable("Today Thabo")
        result = run_recurring_billing(self.today, commit=True)
        self.assertEqual(result["counts"]["invoices_created"], 1)

    def test_a_past_date_is_still_allowed(self):
        """Catch-up after a missed day is the whole reason those helpers
        use `<=`, so a past date must keep working."""
        self._billable("Backdated Bongi", due=self.today - datetime.timedelta(days=5))
        result = run_recurring_billing(self.today - datetime.timedelta(days=3), commit=True)
        self.assertEqual(result["counts"]["invoices_created"], 1)

    def test_a_preview_may_look_at_a_future_date(self):
        """Which is what --date was documented for."""
        self._billable("Peek Pearl")
        result = run_recurring_billing(self.today + datetime.timedelta(days=90), commit=False)
        self.assertEqual(result["counts"]["invoices_created"], 1)
        self.assertEqual(Invoice.objects.count(), 0)


class RunApiTests(_RunTestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.staff = User.objects.create_user(
            username="runner", password="pw-for-tests", role=User.Role.ACCOUNTS
        )
        self.client.force_authenticate(self.staff)

    def test_a_busy_run_is_a_409_not_a_500(self):
        """A second Run click is the ordinary way to get here, so it has to
        read as "wait", not "the server broke"."""
        self._billable("Busy Bheki")
        with mock.patch("billing.recurring._try_run_lock", return_value=False):
            res = self.client.post(
                "/api/recurring-billing/run/", {"date": self.today.isoformat()}, format="json"
            )
        self.assertEqual(res.status_code, 409)
        self.assertIn("already in progress", str(res.data))

    def test_a_future_date_from_the_api_is_a_400(self):
        self._billable("Ahead Ayanda")
        future = (self.today + datetime.timedelta(days=30)).isoformat()
        res = self.client.post("/api/recurring-billing/run/", {"date": future}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(Invoice.objects.count(), 0)

    def test_a_normal_run_still_works(self):
        self._billable("Normal Nandi")
        res = self.client.post(
            "/api/recurring-billing/run/", {"date": self.today.isoformat()}, format="json"
        )
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(Invoice.objects.count(), 1)


class StaleBillingDateTests(_RunTestCase):
    def test_a_customer_billed_mid_run_is_not_billed_again(self):
        """The re-read: each customer is locked and re-read inside their own
        transaction, so the due test sees committed state rather than a
        snapshot taken before the first invoice was written."""
        customer = self._billable("Concurrent Cleo")
        # Simulate another run having billed them after the id list was
        # taken but before this customer's turn came round.
        config = customer.billing_config
        config.next_billing_date = self.today + datetime.timedelta(days=30)
        config.save(update_fields=["next_billing_date"])

        result = run_recurring_billing(self.today, commit=True)
        self.assertEqual(result["counts"]["invoices_created"], 0)
        self.assertEqual(Invoice.objects.count(), 0)

    def test_a_customer_whose_billing_was_switched_off_mid_run_is_skipped(self):
        customer = self._billable("Optout Ofentse")
        config = customer.billing_config
        config.billing_enabled = False
        config.save(update_fields=["billing_enabled"])
        result = run_recurring_billing(self.today, commit=True)
        self.assertEqual(result["counts"]["invoices_created"], 0)

    def test_a_customer_that_vanishes_between_the_list_and_its_turn_is_skipped(self):
        """The re-read returns None rather than raising, so a customer
        deleted (or opted out) mid-run is stepped over without being
        counted as a failure that marks the whole run FAILED."""
        self._billable("Kept Kagiso")
        doomed = self._billable("Doomed Dineo")
        doomed_pk = doomed.pk

        real_get_list = None

        def _list_including_a_ghost(*args, **kwargs):
            # Take the real list, then delete one of them, so the loop is
            # handed an id that no longer resolves.
            ids = list(real_get_list(*args, **kwargs))
            Customer.objects.filter(pk=doomed_pk).delete()
            return ids

        from billing import recurring as recurring_module

        real_get_list = recurring_module._billable_customer_ids
        with mock.patch.object(
            recurring_module, "_billable_customer_ids", side_effect=_list_including_a_ghost
        ):
            result = run_recurring_billing(self.today, commit=True)

        self.assertEqual(result["counts"]["invoices_created"], 1)
        self.assertEqual(result["status"], RecurringBillingRun.Status.PROCESSED)
