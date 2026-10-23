"""Ending a service on its billing end date, and cancelling the customer.

Service.end_date used to be decoration -- staff could set it and the date
would pass with the customer still online and still billed.
"""
import datetime
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from billing.cancellations import apply_due_cancellations, due_services
from billing.models import Service, Tariff
from customers.models import Customer


class CancellationTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.tariff = Tariff.objects.create(name="T", price=Decimal("1"),
                                            speed_download_kbps=1024, speed_upload_kbps=1024)

    def _service(self, customer, end_date=None, status=Service.Status.ACTIVE):
        return Service.objects.create(
            customer=customer, tariff=self.tariff, status=status,
            start_date="2026-01-01", end_date=end_date,
        )

    def _customer(self, name="Leaving Len"):
        return Customer.objects.create(full_name=name, email=f"{name.split()[0].lower()}@x.com",
                                       status=Customer.Status.ACTIVE)

    # ---- the service ends ------------------------------------------------

    def test_a_service_ending_today_is_terminated(self):
        customer = self._customer()
        service = self._service(customer, end_date=self.today)
        ended, cancelled = apply_due_cancellations(as_of=self.today)
        service.refresh_from_db()
        self.assertEqual(service.status, Service.Status.TERMINATED)
        self.assertEqual(len(ended), 1)

    def test_a_future_end_date_is_left_alone(self):
        customer = self._customer()
        service = self._service(customer, end_date=self.today + datetime.timedelta(days=3))
        apply_due_cancellations(as_of=self.today)
        service.refresh_from_db()
        self.assertEqual(service.status, Service.Status.ACTIVE)

    def test_a_missed_date_still_applies(self):
        """A cancellation dated Monday must still happen if the job didn't run
        until Thursday -- the same lesson as tariff changes."""
        customer = self._customer()
        service = self._service(customer, end_date=self.today - datetime.timedelta(days=4))
        apply_due_cancellations(as_of=self.today)
        service.refresh_from_db()
        self.assertEqual(service.status, Service.Status.TERMINATED)

    def test_no_end_date_means_never(self):
        customer = self._customer()
        service = self._service(customer, end_date=None)
        apply_due_cancellations(as_of=self.today)
        service.refresh_from_db()
        self.assertEqual(service.status, Service.Status.ACTIVE)

    def test_dry_run_changes_nothing(self):
        customer = self._customer()
        service = self._service(customer, end_date=self.today)
        ended, cancelled = apply_due_cancellations(as_of=self.today, commit=False)
        service.refresh_from_db()
        self.assertEqual(service.status, Service.Status.ACTIVE)
        self.assertEqual(len(ended), 1, "a dry run should still report what it would do")

    def test_dry_run_names_the_customers_it_would_cancel(self):
        """The consequence staff most need to see is the one it hid.

        customers_fully_ended asks the database which customers have no
        service left running -- and in a dry run nothing has been
        terminated, so the answer was always "none". The preview listed
        the services and reported an empty cancellation list, so the
        account-level consequence (customer marked Inactive, which is
        what "cancelled" means to staff) was invisible in the one screen
        that exists to show it before committing. The only way to find
        out was to commit.
        """
        customer = self._customer()
        self._service(customer, end_date=self.today)

        ended, cancelled = apply_due_cancellations(as_of=self.today, commit=False)

        self.assertEqual(len(ended), 1)
        self.assertEqual(
            [name for _pk, name in cancelled], [customer.full_name],
            "the dry run did not say the customer would be cancelled",
        )

    def test_dry_run_does_not_claim_a_customer_who_keeps_a_service(self):
        """It must predict, not over-predict."""
        customer = self._customer()
        self._service(customer, end_date=self.today)
        self._service(customer, end_date=None)

        ended, cancelled = apply_due_cancellations(as_of=self.today, commit=False)

        self.assertEqual(len(ended), 1)
        self.assertEqual(cancelled, [])

    def test_the_dry_run_predicts_exactly_what_the_commit_does(self):
        """The point of a preview, stated as a test."""
        staying = self._customer("Staying Stan")
        self._service(staying, end_date=self.today)
        self._service(staying, end_date=None)
        leaving = self._customer("Leaving Len")
        self._service(leaving, end_date=self.today)

        predicted_ended, predicted_cancelled = apply_due_cancellations(
            as_of=self.today, commit=False
        )
        actual_ended, actual_cancelled = apply_due_cancellations(as_of=self.today, commit=True)

        self.assertEqual(predicted_ended, actual_ended)
        self.assertEqual(predicted_cancelled, actual_cancelled)
        self.assertEqual([name for _pk, name in actual_cancelled], ["Leaving Len"])

    # ---- the customer is cancelled ---------------------------------------

    def test_the_customer_is_cancelled_when_their_last_service_ends(self):
        customer = self._customer()
        self._service(customer, end_date=self.today)
        ended, cancelled = apply_due_cancellations(as_of=self.today)
        customer.refresh_from_db()
        self.assertEqual(customer.status, Customer.Status.INACTIVE)
        self.assertEqual(len(cancelled), 1)

    def test_a_customer_with_another_live_service_is_not_cancelled(self):
        customer = self._customer()
        self._service(customer, end_date=self.today)
        self._service(customer, end_date=None)
        ended, cancelled = apply_due_cancellations(as_of=self.today)
        customer.refresh_from_db()
        self.assertEqual(customer.status, Customer.Status.ACTIVE)
        self.assertEqual(cancelled, [])

    def test_both_services_ending_cancels_them(self):
        customer = self._customer()
        self._service(customer, end_date=self.today)
        self._service(customer, end_date=self.today - datetime.timedelta(days=1))
        apply_due_cancellations(as_of=self.today)
        customer.refresh_from_db()
        self.assertEqual(customer.status, Customer.Status.INACTIVE)

    def test_a_customer_with_no_services_is_never_touched(self):
        """Mid-signup, before their first service exists. "All their services
        have ended" and "they never had one" are different situations."""
        customer = self._customer("Signing Up")
        other = self._customer("Leaving Len")
        self._service(other, end_date=self.today)
        apply_due_cancellations(as_of=self.today)
        customer.refresh_from_db()
        self.assertEqual(customer.status, Customer.Status.ACTIVE)

    def test_running_twice_does_the_work_once(self):
        customer = self._customer()
        self._service(customer, end_date=self.today)
        apply_due_cancellations(as_of=self.today)
        ended, cancelled = apply_due_cancellations(as_of=self.today)
        self.assertEqual((ended, cancelled), ([], []))

    def test_an_already_cancelled_customer_is_not_reported_again(self):
        customer = self._customer()
        customer.status = Customer.Status.INACTIVE
        customer.save()
        self._service(customer, end_date=self.today)
        ended, cancelled = apply_due_cancellations(as_of=self.today)
        self.assertEqual(len(ended), 1)
        self.assertEqual(cancelled, [])

    def test_terminating_goes_through_save_so_radius_is_rewritten(self):
        """The signals ARE the work -- a bulk update would leave a cancelled
        customer online indefinitely."""
        from radiusauth.models import RadCheck

        customer = self._customer()
        service = self._service(customer, end_date=self.today)
        service.radius_username = "len"
        service.radius_password = "pw"
        service.save()
        self.assertTrue(RadCheck.objects.filter(username="len", attribute="Cleartext-Password").exists())

        apply_due_cancellations(as_of=self.today)
        self.assertFalse(
            RadCheck.objects.filter(username="len", attribute="Cleartext-Password").exists(),
            "a cancelled service must not still be able to authenticate",
        )
        self.assertTrue(RadCheck.objects.filter(username="len", attribute="Auth-Type").exists())

    def test_the_end_date_is_the_first_day_WITHOUT_service(self):
        """Set the 31st and they run through the 30th, stopping at 00:00 on
        the 31st. Asserted explicitly because the off-by-one here is a day of
        service somebody either paid for and didn't get, or got free."""
        customer = self._customer()
        end = datetime.date(2026, 8, 31)
        service = self._service(customer, end_date=end)

        # The night of the 30th: still theirs.
        apply_due_cancellations(as_of=end - datetime.timedelta(days=1))
        service.refresh_from_db()
        self.assertEqual(service.status, Service.Status.ACTIVE,
                         "the 30th is their last full day and must not be cut short")

        # The job runs at 00:01 on the 31st.
        apply_due_cancellations(as_of=end)
        service.refresh_from_db()
        self.assertEqual(service.status, Service.Status.TERMINATED)
