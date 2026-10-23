"""A customer paid in full, went into credit, and stayed cut off.

WHAT WAS BROKEN

Two breaks in one chain, and each alone was enough:

  * the blocking pass suspended a service and left no mark saying it had
    done so;
  * the only restore path in the codebase keys on a different mark
    (`auto_suspended_with_customer`) and fires on a CUSTOMER status
    change, which the billing run never makes.

And nothing anywhere watched payments. No payment, of any size, had ever
reconnected anybody.

THE TWO THINGS THAT MUST BOTH HOLD

Reconnect the customer who paid. Never reconnect the line a person took
down on purpose. Most of the tests here are the second one, because it
is the failure nobody reports -- a line quietly coming back on is not a
support call, it is a customer browsing on a connection somebody
disabled for a reason.

Every date and amount is pinned. Nothing here touches RADIUS.
"""
import datetime
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase

from customers.models import Customer

from . import reactivation
from .models import CustomerBillingConfig, Payment, Service, Tariff


class ReactivationTests(TestCase):
    def setUp(self):
        self.tariff = Tariff.objects.create(
            name="Home 100", price=Decimal("899.00"), tax_rate_pct=Decimal("15.00"),
        )
        self.customer = self._customer("Lucille Slabbert", "CUS-009100", "516.35")
        self.service = self._service(self.customer)

    # --- fixtures ---------------------------------------------------------

    def _customer(self, name, code, balance, minimum="0.00"):
        customer = Customer.objects.create(
            full_name=name, customer_id=code, email=f"{code.lower()}@example.com",
            status=Customer.Status.ACTIVE,
        )
        Customer.objects.filter(pk=customer.pk).update(balance=Decimal(balance))
        customer.refresh_from_db()
        config = CustomerBillingConfig.for_customer(customer)
        config.minimum_balance = Decimal(minimum)
        config.auto_create_invoices = False
        config.save()
        return customer

    def _service(self, customer, *, suspended=True, by_billing=True):
        service = Service.objects.create(
            customer=customer, tariff=self.tariff,
            status=Service.Status.ACTIVE, start_date=datetime.date(2026, 9, 14),
        )
        if suspended:
            Service.objects.filter(pk=service.pk).update(
                status=Service.Status.SUSPENDED, suspended_for_nonpayment=by_billing
            )
            service.refresh_from_db()
        return service

    def _pay(self, customer, amount, **kwargs):
        """A payment, and the balance movement that goes with it."""
        payment = Payment.objects.create(customer=customer, amount=Decimal(amount), **kwargs)
        return payment

    def _set_balance(self, customer, balance):
        Customer.objects.filter(pk=customer.pk).update(balance=Decimal(balance))
        customer.refresh_from_db()

    # --- the rule ---------------------------------------------------------

    def test_a_cleared_balance_is_a_candidate(self):
        self._set_balance(self.customer, "-103.65")

        found = reactivation.candidates()

        self.assertEqual([s.pk for s, _ in found], [self.service.pk])

    def test_still_owing_is_not(self):
        """A partial payment that leaves them over the threshold keeps them
        off. The suspension rule and this one read the same field the same
        way, so they cannot disagree."""
        self._set_balance(self.customer, "300.00")

        self.assertEqual(reactivation.candidates(), [])

    def test_the_minimum_balance_cushion_is_honoured(self):
        """blocking_candidate_services suspends only when balance exceeds
        minimum_balance, so a customer inside their cushion is not
        suspendable and must not be kept off."""
        generous = self._customer("Cushion Cora", "CUS-009101", "50.00", minimum="100.00")
        service = self._service(generous)

        found = reactivation.candidates()

        self.assertIn(service.pk, [s.pk for s, _ in found])

    def test_exactly_on_the_minimum_is_clear(self):
        """The suspension test is `balance <= minimum_balance -> not
        suspendable`. The boundary belongs to the customer."""
        edge = self._customer("Edge Eddie", "CUS-009102", "100.00", minimum="100.00")
        service = self._service(edge)

        self.assertIn(service.pk, [s.pk for s, _ in reactivation.candidates()])

    # --- what must never be touched ---------------------------------------

    def test_a_service_suspended_by_hand_is_never_reconnected(self):
        """THE ONE THAT MATTERS MOST. Abuse, hardware recovery, a dispute,
        a customer who asked to be paused -- none of those are undone by
        money arriving, and nothing about Service.status can tell them
        apart from a billing suspension."""
        manual = self._customer("Manual Marius", "CUS-009103", "-500.00")
        self._service(manual, by_billing=False)

        self.assertEqual(reactivation.candidates(customer=manual), [])

    def test_the_sweep_can_see_unflagged_services_when_asked(self):
        """Explicitly, and only explicitly -- for the services suspended
        before the flag existed."""
        old = self._customer("Old Olga", "CUS-009104", "-20.00")
        service = self._service(old, by_billing=False)

        self.assertEqual(reactivation.candidates(customer=old), [])
        self.assertIn(
            service.pk,
            [s.pk for s, _ in reactivation.candidates(customer=old, include_unflagged=True)],
        )

    def test_an_active_service_is_left_alone(self):
        running = self._customer("Running Riaan", "CUS-009105", "-10.00")
        self._service(running, suspended=False)

        self.assertEqual(reactivation.candidates(customer=running), [])

    def test_changing_the_status_by_hand_clears_the_billing_mark(self):
        """Staff turn the line on, then later take it down themselves. A
        payment must not undo that second decision."""
        self.service.status = Service.Status.ACTIVE
        self.service.save()
        self.service.refresh_from_db()

        self.assertFalse(self.service.suspended_for_nonpayment)

    # --- doing it ---------------------------------------------------------

    def test_reactivating_goes_through_save_so_the_signals_fire(self):
        """.update() would mark the row ACTIVE and leave the customer
        walled-garden'd in reality -- the enforcement hangs off post_save."""
        self._set_balance(self.customer, "-103.65")

        with mock.patch.object(Service, "save", autospec=True) as saved:
            reactivation.reactivate(self.service)

        self.assertTrue(saved.called)

    def test_reactivating_clears_the_mark(self):
        self._set_balance(self.customer, "-103.65")

        reactivation.reactivate(self.service)
        self.service.refresh_from_db()

        self.assertEqual(self.service.status, Service.Status.ACTIVE)
        self.assertFalse(self.service.suspended_for_nonpayment)


class PaymentReconnectsTests(TestCase):
    """The signal: a payment lands and the line comes back within seconds."""

    def setUp(self):
        self.tariff = Tariff.objects.create(
            name="Home 100", price=Decimal("899.00"), tax_rate_pct=Decimal("15.00"),
        )
        self.customer = Customer.objects.create(
            full_name="Lucille Slabbert", customer_id="CUS-009200",
            email="lucille@example.com", status=Customer.Status.ACTIVE,
        )
        CustomerBillingConfig.for_customer(self.customer)
        self.service = Service.objects.create(
            customer=self.customer, tariff=self.tariff,
            status=Service.Status.ACTIVE, start_date=datetime.date(2026, 9, 14),
        )
        Service.objects.filter(pk=self.service.pk).update(
            status=Service.Status.SUSPENDED, suspended_for_nonpayment=True
        )

    def _balance(self, value):
        Customer.objects.filter(pk=self.customer.pk).update(balance=Decimal(value))

    def test_a_payment_that_clears_the_balance_reconnects(self):
        self._balance("-103.65")

        with self.captureOnCommitCallbacks(execute=True):
            Payment.objects.create(customer=self.customer, amount=Decimal("620.00"))

        self.service.refresh_from_db()
        self.assertEqual(self.service.status, Service.Status.ACTIVE)

    def test_a_payment_that_does_not_clear_it_leaves_them_off(self):
        self._balance("300.00")

        with self.captureOnCommitCallbacks(execute=True):
            Payment.objects.create(customer=self.customer, amount=Decimal("200.00"))

        self.service.refresh_from_db()
        self.assertEqual(self.service.status, Service.Status.SUSPENDED)

    def test_an_imported_payment_reconnects_nobody(self):
        """THE LEDGER IMPORT. It back-dates years of history one row at a
        time; without this guard, importing a customer's past would
        reconnect them on money that arrived in 2024."""
        from .statements import IMPORT_NOTE_PREFIX

        self._balance("-103.65")

        with self.captureOnCommitCallbacks(execute=True):
            Payment.objects.create(
                customer=self.customer, amount=Decimal("620.00"),
                note=f"{IMPORT_NOTE_PREFIX}RCP2024-01-00001",
            )

        self.service.refresh_from_db()
        self.assertEqual(self.service.status, Service.Status.SUSPENDED)

    def test_a_failed_reconnection_does_not_break_the_payment(self):
        """A reconnection that fails is a support call. A payment that
        fails to record is an accounting problem."""
        self._balance("-103.65")

        with mock.patch("billing.reactivation.reactivate", side_effect=RuntimeError("radius down")):
            with self.captureOnCommitCallbacks(execute=True):
                payment = Payment.objects.create(customer=self.customer, amount=Decimal("620.00"))

        self.assertTrue(Payment.objects.filter(pk=payment.pk).exists())


class ApplyReactivationsCommandTests(TestCase):
    def setUp(self):
        self.tariff = Tariff.objects.create(
            name="Home 100", price=Decimal("899.00"), tax_rate_pct=Decimal("15.00"),
        )
        self.customer = Customer.objects.create(
            full_name="Lucille Slabbert", customer_id="CUS-009300",
            email="lucille3@example.com", status=Customer.Status.ACTIVE,
        )
        Customer.objects.filter(pk=self.customer.pk).update(balance=Decimal("-103.65"))
        CustomerBillingConfig.for_customer(self.customer)
        self.service = Service.objects.create(
            customer=self.customer, tariff=self.tariff,
            status=Service.Status.ACTIVE, start_date=datetime.date(2026, 9, 14),
        )
        Service.objects.filter(pk=self.service.pk).update(
            status=Service.Status.SUSPENDED, suspended_for_nonpayment=True
        )

    def _run(self, *args):
        out, err = StringIO(), StringIO()
        call_command("apply_reactivations", *args, stdout=out, stderr=err)
        return out.getvalue() + err.getvalue()

    def test_a_dry_run_changes_nothing(self):
        output = self._run()

        self.service.refresh_from_db()
        self.assertEqual(self.service.status, Service.Status.SUSPENDED)
        self.assertIn("Would reconnect 1", output)
        self.assertIn("Lucille Slabbert", output)

    def test_commit_reconnects(self):
        self._run("--commit")

        self.service.refresh_from_db()
        self.assertEqual(self.service.status, Service.Status.ACTIVE)

    def test_the_unflagged_sweep_warns_before_it_lists(self):
        Service.objects.filter(pk=self.service.pk).update(suspended_for_nonpayment=False)

        output = self._run("--include-unflagged")

        self.assertIn("INCLUDING UNFLAGGED", output)
        self.assertIn("UNFLAGGED", output)
        self.service.refresh_from_db()
        self.assertEqual(self.service.status, Service.Status.SUSPENDED)

    def test_one_customer_can_be_targeted(self):
        other = Customer.objects.create(
            full_name="Other Otto", customer_id="CUS-009301",
            email="otto@example.com", status=Customer.Status.ACTIVE,
        )
        Customer.objects.filter(pk=other.pk).update(balance=Decimal("-5.00"))
        CustomerBillingConfig.for_customer(other)
        theirs = Service.objects.create(
            customer=other, tariff=self.tariff,
            status=Service.Status.ACTIVE, start_date=datetime.date(2026, 9, 14),
        )
        Service.objects.filter(pk=theirs.pk).update(
            status=Service.Status.SUSPENDED, suspended_for_nonpayment=True
        )

        self._run("--client-code", "CUS-009300", "--commit")

        self.service.refresh_from_db()
        theirs.refresh_from_db()
        self.assertEqual(self.service.status, Service.Status.ACTIVE)
        self.assertEqual(theirs.status, Service.Status.SUSPENDED)

    def test_nothing_to_do_says_so(self):
        Customer.objects.filter(pk=self.customer.pk).update(balance=Decimal("900.00"))

        self.assertIn("Nothing to reconnect", self._run())
