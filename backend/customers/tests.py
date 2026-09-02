"""Customer status <-> Service status, in both directions.

The case that prompted these: suspend a customer, then bring the line back
from Services -> Edit -> Active. The service went active, the customer record
stayed suspended, and the page showed a red Suspended badge directly above a
green Active service.
"""
from decimal import Decimal

from django.test import TestCase

from billing.models import Service, Tariff
from customers.models import Customer


class StatusPropagationTests(TestCase):
    def setUp(self):
        self.tariff = Tariff.objects.create(
            name="Test 4 Mbps", price=Decimal("499.00"),
            speed_download_kbps=4096, speed_upload_kbps=4096,
        )
        self.customer = Customer.objects.create(
            full_name="Skybre Test", email="test@example.com",
            status=Customer.Status.ACTIVE,
        )

    def _service(self, status=Service.Status.ACTIVE):
        return Service.objects.create(
            customer=self.customer, tariff=self.tariff, status=status,
            start_date="2026-08-21",
        )

    # --- Customer -> Service (existing behaviour, guarded) ----------------

    def test_suspending_customer_suspends_active_services(self):
        service = self._service()
        self.customer.status = Customer.Status.SUSPENDED
        self.customer.save()
        service.refresh_from_db()
        self.assertEqual(service.status, Service.Status.SUSPENDED)
        self.assertTrue(service.auto_suspended_with_customer)

    def test_reactivating_customer_leaves_a_non_payment_suspension_alone(self):
        service = self._service(status=Service.Status.SUSPENDED)
        self.customer.status = Customer.Status.SUSPENDED
        self.customer.save()
        self.customer.status = Customer.Status.ACTIVE
        self.customer.save()
        service.refresh_from_db()
        self.assertEqual(service.status, Service.Status.SUSPENDED)

    # --- Service -> Customer (the fix) ------------------------------------

    def test_restoring_the_service_by_hand_lifts_the_customer(self):
        service = self._service()
        self.customer.status = Customer.Status.SUSPENDED
        self.customer.save()

        service.refresh_from_db()
        service.status = Service.Status.ACTIVE
        service.save()

        self.customer.refresh_from_db()
        self.assertEqual(self.customer.status, Customer.Status.ACTIVE)
        service.refresh_from_db()
        self.assertFalse(service.auto_suspended_with_customer)

    def test_restoring_one_line_does_not_restore_the_others(self):
        kept, other = self._service(), self._service()
        self.customer.status = Customer.Status.SUSPENDED
        self.customer.save()

        kept.refresh_from_db()
        kept.status = Service.Status.ACTIVE
        kept.save()

        self.customer.refresh_from_db()
        self.assertEqual(self.customer.status, Customer.Status.ACTIVE)
        other.refresh_from_db()
        self.assertEqual(other.status, Service.Status.SUSPENDED)

    def test_a_manual_suspension_clears_the_flag(self):
        """The stale-flag bug: suspended with the customer, restored by hand,
        then suspended again for non-payment. Reactivating the customer must
        not hand that line back."""
        service = self._service()
        self.customer.status = Customer.Status.SUSPENDED
        self.customer.save()

        service.refresh_from_db()
        service.status = Service.Status.ACTIVE
        service.save()                                  # customer -> active

        service.refresh_from_db()
        service.status = Service.Status.SUSPENDED       # now for non-payment
        service.save()
        service.refresh_from_db()
        self.assertFalse(service.auto_suspended_with_customer)

        self.customer.refresh_from_db()
        self.customer.status = Customer.Status.SUSPENDED
        self.customer.save()
        self.customer.status = Customer.Status.ACTIVE
        self.customer.save()

        service.refresh_from_db()
        self.assertEqual(service.status, Service.Status.SUSPENDED)

    def test_an_active_customer_is_not_touched(self):
        service = self._service(status=Service.Status.SUSPENDED)
        service.status = Service.Status.ACTIVE
        service.save()
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.status, Customer.Status.ACTIVE)

    def test_suspending_a_service_never_suspends_the_customer(self):
        service = self._service()
        service.status = Service.Status.SUSPENDED
        service.save()
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.status, Customer.Status.ACTIVE)


class BadDebtTests(TestCase):
    """Bad Debt: written off, and cut off.

    Deliberately its own status rather than a shade of Suspended. Suspended is
    temporary and reversible by paying; Inactive means they left with nothing
    outstanding; Bad Debt means money we have stopped expecting. Three
    different conversations, so three different labels.
    """

    def setUp(self):
        self.tariff = Tariff.objects.create(
            name="T", price=Decimal("1"), speed_download_kbps=1024, speed_upload_kbps=1024
        )
        self.customer = Customer.objects.create(
            full_name="Owes Us", email="owes@x.com", status=Customer.Status.ACTIVE
        )

    def _service(self):
        return Service.objects.create(
            customer=self.customer, tariff=self.tariff, status=Service.Status.ACTIVE,
            start_date="2026-08-01",
        )

    def test_bad_debt_cuts_the_service_off(self):
        service = self._service()
        self.customer.status = Customer.Status.BAD_DEBT
        self.customer.save()
        service.refresh_from_db()
        self.assertEqual(service.status, Service.Status.SUSPENDED)
        self.assertTrue(service.auto_suspended_with_customer)

    def test_going_back_to_active_restores_them(self):
        service = self._service()
        self.customer.status = Customer.Status.BAD_DEBT
        self.customer.save()
        self.customer.status = Customer.Status.ACTIVE
        self.customer.save()
        service.refresh_from_db()
        self.assertEqual(service.status, Service.Status.ACTIVE)

    def test_restoring_a_service_does_NOT_clear_the_write_off(self):
        """A suspension is purely about connectivity, so a live service
        contradicts it. Bad Debt is about money owed -- silently clearing it
        because somebody turned a line back on would erase an accounting
        decision that was never made here."""
        service = self._service()
        self.customer.status = Customer.Status.BAD_DEBT
        self.customer.save()

        service.refresh_from_db()
        service.status = Service.Status.ACTIVE
        service.save()

        self.customer.refresh_from_db()
        self.assertEqual(self.customer.status, Customer.Status.BAD_DEBT)

    def test_they_are_not_on_the_offline_call_list(self):
        """They're offline because we cut them off. "Can we help?" is the
        wrong call to make to somebody we've written off."""
        import datetime

        from django.utils import timezone

        from radiusauth.models import RadAcct
        from radiusauth.offline import recently_offline

        service = self._service()
        service.radius_username = "owes"
        service.save()
        RadAcct.objects.create(
            acctsessionid="s", acctuniqueid="s", username="owes", nasipaddress="10.0.0.1",
            acctstarttime=timezone.now() - datetime.timedelta(hours=5),
            acctstoptime=timezone.now() - datetime.timedelta(hours=2),
            acctupdatetime=timezone.now() - datetime.timedelta(hours=2),
        )
        self.customer.status = Customer.Status.BAD_DEBT
        self.customer.save()
        self.assertEqual(recently_offline(list(Customer.objects.all())), [])

    def test_it_is_a_real_choice_the_api_accepts(self):
        from customers.serializers import CustomerSerializer

        serializer = CustomerSerializer(self.customer, data={"status": "bad_debt"}, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
