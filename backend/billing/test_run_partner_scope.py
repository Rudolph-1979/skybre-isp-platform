"""A run scoped to one partner must stay inside that partner.

Finance -> Recurring Billing lets staff scope a Run to selected partners,
and RecurringBillingRun.partners records which. Invoicing honoured it --
_billable_customer_ids filters on it -- but the two jobs a committed run
calls FIRST did not:

    apply_due_tariff_changes(as_of=run_date)
    apply_due_cancellations(as_of=run_date, commit=True)

Both walk Service.objects platform-wide. So a run scoped to Acme
terminated a Rival customer's service, marked that customer Cancelled,
and switched their tariff -- disconnecting somebody who belongs to a
reseller the operator did not run billing for, with nothing on the run
row to say it happened. The reseller-scoped staff member who clicked Run
could not have seen those customers on any page in the platform.

The tests below scope a run to one partner and assert the other
partner's customer is untouched, plus the ordinary unscoped case still
does the work for everybody.
"""
import datetime
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from billing.models import CustomerBillingConfig, Service, Tariff
from billing.recurring import run_recurring_billing
from customers.models import Customer, Partner


class RunPartnerScopeTestCase(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.yesterday = self.today - datetime.timedelta(days=1)
        self.acme = Partner.objects.create(name="Acme Fibre")
        self.rival = Partner.objects.create(name="Rival Networks")
        self.tariff = Tariff.objects.create(
            name="Home 20", price=Decimal("500.00"), tax_rate_pct=Decimal("15.00"),
            speed_download_kbps=20480, speed_upload_kbps=10240,
        )
        self.faster = Tariff.objects.create(
            name="Home 100", price=Decimal("900.00"), tax_rate_pct=Decimal("15.00"),
            speed_download_kbps=102400, speed_upload_kbps=51200,
        )

    def _customer(self, name, partner):
        customer = Customer.objects.create(
            full_name=name, email=f"{name.split()[0].lower()}@example.com",
            status=Customer.Status.ACTIVE, partner=partner,
        )
        config = CustomerBillingConfig.for_customer(customer)
        config.billing_enabled = True
        config.next_billing_date = self.today
        config.save()
        return customer

    def _ending_service(self, customer):
        """A service whose end date arrived yesterday -- due for cancellation."""
        return Service.objects.create(
            customer=customer, tariff=self.tariff, status=Service.Status.ACTIVE,
            start_date=self.today - datetime.timedelta(days=90), end_date=self.yesterday,
        )

    def _switching_service(self, customer):
        """A service with a tariff change booked for yesterday -- due to apply."""
        return Service.objects.create(
            customer=customer, tariff=self.tariff, status=Service.Status.ACTIVE,
            start_date=self.today - datetime.timedelta(days=90),
            pending_tariff=self.faster, pending_tariff_date=self.yesterday,
        )


class CancellationsStayInsideTheScopedPartner(RunPartnerScopeTestCase):
    def test_the_other_partners_service_is_not_terminated(self):
        mine = self._ending_service(self._customer("Acme Annie", self.acme))
        theirs = self._ending_service(self._customer("Rival Rita", self.rival))

        run_recurring_billing(self.today, partner_ids=[self.acme.pk], commit=True)

        mine.refresh_from_db()
        theirs.refresh_from_db()
        self.assertEqual(mine.status, Service.Status.TERMINATED)
        self.assertEqual(
            theirs.status, Service.Status.ACTIVE,
            "a run scoped to Acme disconnected a Rival customer",
        )

    def test_the_other_partners_customer_is_not_cancelled(self):
        rival_customer = self._customer("Rival Rita", self.rival)
        self._ending_service(rival_customer)
        self._ending_service(self._customer("Acme Annie", self.acme))

        run_recurring_billing(self.today, partner_ids=[self.acme.pk], commit=True)

        rival_customer.refresh_from_db()
        self.assertEqual(
            rival_customer.status, Customer.Status.ACTIVE,
            "a run scoped to Acme marked a Rival customer Cancelled",
        )

    def test_an_unscoped_run_still_ends_everybodys_due_services(self):
        """The scoping must not become a way to miss the ordinary case."""
        mine = self._ending_service(self._customer("Acme Annie", self.acme))
        theirs = self._ending_service(self._customer("Rival Rita", self.rival))

        run_recurring_billing(self.today, commit=True)

        mine.refresh_from_db()
        theirs.refresh_from_db()
        self.assertEqual(mine.status, Service.Status.TERMINATED)
        self.assertEqual(theirs.status, Service.Status.TERMINATED)


class TariffChangesStayInsideTheScopedPartner(RunPartnerScopeTestCase):
    def test_the_other_partners_tariff_change_is_not_applied(self):
        mine = self._switching_service(self._customer("Acme Annie", self.acme))
        theirs = self._switching_service(self._customer("Rival Rita", self.rival))

        run_recurring_billing(self.today, partner_ids=[self.acme.pk], commit=True)

        mine.refresh_from_db()
        theirs.refresh_from_db()
        self.assertEqual(mine.tariff_id, self.faster.pk)
        self.assertEqual(
            theirs.tariff_id, self.tariff.pk,
            "a run scoped to Acme changed a Rival customer's plan",
        )
        self.assertEqual(
            theirs.pending_tariff_id, self.faster.pk,
            "the booking was consumed by a run that should not have seen it",
        )

    def test_an_unscoped_run_still_applies_everybodys_due_changes(self):
        mine = self._switching_service(self._customer("Acme Annie", self.acme))
        theirs = self._switching_service(self._customer("Rival Rita", self.rival))

        run_recurring_billing(self.today, commit=True)

        mine.refresh_from_db()
        theirs.refresh_from_db()
        self.assertEqual(mine.tariff_id, self.faster.pk)
        self.assertEqual(theirs.tariff_id, self.faster.pk)


class CustomersWithNoPartnerTests(RunPartnerScopeTestCase):
    """Most of Skybre's own customers have no partner at all.

    They must not be swept into a partner-scoped run (they are not that
    reseller's), and must still be handled by an unscoped one.
    """

    def test_a_partnerless_customer_is_left_alone_by_a_scoped_run(self):
        direct = self._ending_service(self._customer("Direct Dora", None))

        run_recurring_billing(self.today, partner_ids=[self.acme.pk], commit=True)

        direct.refresh_from_db()
        self.assertEqual(direct.status, Service.Status.ACTIVE)

    def test_a_partnerless_customer_is_handled_by_an_unscoped_run(self):
        direct = self._ending_service(self._customer("Direct Dora", None))

        run_recurring_billing(self.today, commit=True)

        direct.refresh_from_db()
        self.assertEqual(direct.status, Service.Status.TERMINATED)
