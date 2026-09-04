"""A customer who is off must not still be connected.

Customer.OFF_STATUSES exists so "nothing has to remember to add a new one
to four separate checks" -- its own words. Two of those checks forgot it:

  * customers.signals' cascade hardcoded (SUSPENDED, BAD_DEBT), omitting
    INACTIVE. INACTIVE is the status meaning "they left", so it is the
    natural thing to set when a customer cancels -- and doing that left
    every service ACTIVE with a live Cleartext-Password row in radcheck,
    so the line kept full internet indefinitely.
  * check_suspension_enforcement, the reconciliation that is supposed to
    CATCH exactly that, filtered on SUSPENDED alone -- so a Bad Debt or
    Inactive customer with live services never appeared in its report and
    --fix never repaired them.

And radiusauth.offline EXCLUDES customers in OFF_STATUSES from the
recently-offline report, by design -- so the one screen that would have
shown a line that should not be up filtered these customers out. Nothing
anywhere would have surfaced it.
"""
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from billing.models import Service, Tariff
from customers.models import Customer
from decimal import Decimal


class OffStatusCascadeTests(TestCase):
    def setUp(self):
        self.tariff = Tariff.objects.create(
            name="Home 20", price=Decimal("500.00"),
            speed_download_kbps=20480, speed_upload_kbps=10240,
        )

    def _customer_with_active_service(self, name, status=Customer.Status.ACTIVE):
        # Slugged off the WHOLE name: radius_username is globally unique,
        # and taking only the first word made every subTest below collide.
        slug = name.lower().replace(" ", "_").replace("-", "_")
        customer = Customer.objects.create(
            full_name=name, email=f"{slug}@example.com", status=status
        )
        service = Service.objects.create(
            customer=customer, tariff=self.tariff, status=Service.Status.ACTIVE,
            start_date="2026-01-01", radius_username=f"pppoe_{slug}",
        )
        return customer, service

    def test_every_off_status_suspends_the_services(self):
        """The table-driven version, so a new off-status cannot be added
        to the constant without this catching an un-updated cascade."""
        for status in Customer.OFF_STATUSES:
            with self.subTest(status=status):
                customer, service = self._customer_with_active_service(f"Off {status}")
                customer.status = status
                customer.save()
                service.refresh_from_db()
                self.assertEqual(
                    service.status, Service.Status.SUSPENDED,
                    f"a customer set to {status} kept an ACTIVE service",
                )
                self.assertTrue(service.auto_suspended_with_customer)

    def test_inactive_specifically_cuts_the_line_off(self):
        """The one that was missing. 'They left' has to mean they left."""
        customer, service = self._customer_with_active_service("Cancelled Cara")
        customer.status = Customer.Status.INACTIVE
        customer.save()
        service.refresh_from_db()
        self.assertEqual(service.status, Service.Status.SUSPENDED)

    def test_inactive_clears_the_radius_password(self):
        """The service status is only half of it -- what actually keeps
        them online is the Cleartext-Password row."""
        from radiusauth.models import RadCheck

        customer, service = self._customer_with_active_service("Radius Rita")
        service.radius_password = "secret-pppoe-pw"
        service.save()
        self.assertTrue(
            RadCheck.objects.filter(
                username=service.radius_username, attribute="Cleartext-Password"
            ).exists()
        )

        customer.status = Customer.Status.INACTIVE
        customer.save()
        self.assertFalse(
            RadCheck.objects.filter(
                username=service.radius_username, attribute="Cleartext-Password"
            ).exists(),
            "a cancelled customer could still authenticate",
        )

    def test_an_active_status_still_restores_only_what_it_took_down(self):
        """The reactivation half must not regress: a service suspended for
        its own reasons stays suspended."""
        customer, cascaded = self._customer_with_active_service("Restored Rudi")
        manual = Service.objects.create(
            customer=customer, tariff=self.tariff, status=Service.Status.ACTIVE,
            start_date="2026-01-01", radius_username="pppoe_rudi2",
        )
        customer.status = Customer.Status.INACTIVE
        customer.save()

        # Now suspend one of them "by hand" -- clearing the cascade flag.
        manual.refresh_from_db()
        manual.auto_suspended_with_customer = False
        manual.save(update_fields=["auto_suspended_with_customer"])

        customer.status = Customer.Status.ACTIVE
        customer.save()
        cascaded.refresh_from_db()
        manual.refresh_from_db()
        self.assertEqual(cascaded.status, Service.Status.ACTIVE)
        self.assertEqual(manual.status, Service.Status.SUSPENDED)


class SuspensionReconciliationTests(TestCase):
    """check_suspension_enforcement is the safety net. It has to see every
    way a customer can be off."""

    def setUp(self):
        self.tariff = Tariff.objects.create(
            name="Home 10", price=Decimal("300.00"),
            speed_download_kbps=10240, speed_upload_kbps=5120,
        )

    def _stranded(self, name, status):
        """A customer who is off but whose service is still ACTIVE -- the
        state the reconciliation exists to find. Written with .update() so
        the cascade does not fix it on the way in."""
        slug = name.lower().replace(" ", "_")
        customer = Customer.objects.create(
            full_name=name, email=f"{slug}@example.com",
            status=Customer.Status.ACTIVE,
        )
        service = Service.objects.create(
            customer=customer, tariff=self.tariff, status=Service.Status.ACTIVE,
            start_date="2026-01-01",
        )
        Customer.objects.filter(pk=customer.pk).update(status=status)
        return customer, service

    def _report(self):
        out = StringIO()
        call_command("check_suspension_enforcement", stdout=out, stderr=StringIO())
        return out.getvalue()

    def test_it_reports_a_stranded_service_for_every_off_status(self):
        for status in Customer.OFF_STATUSES:
            with self.subTest(status=status):
                customer, _ = self._stranded(f"Stranded {status}", status)
                self.assertIn(
                    customer.full_name, self._report(),
                    f"a {status} customer with a live service was not reported",
                )

    def test_a_bad_debt_customer_is_reported(self):
        """Previously invisible: the filter was SUSPENDED only."""
        customer, _ = self._stranded("Writeoff Wanda", Customer.Status.BAD_DEBT)
        self.assertIn("Writeoff Wanda", self._report())

    def test_an_active_customer_is_not_reported(self):
        customer = Customer.objects.create(
            full_name="Paid Up Pat", email="pat@example.com", status=Customer.Status.ACTIVE
        )
        Service.objects.create(
            customer=customer, tariff=self.tariff, status=Service.Status.ACTIVE,
            start_date="2026-01-01",
        )
        self.assertNotIn("Paid Up Pat", self._report())
