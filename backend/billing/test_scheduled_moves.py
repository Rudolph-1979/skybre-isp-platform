"""A partner move and a new service booked for a date; billing as at the
billing date (package #20).

Decided with Rudolph 2026-09-24 for Christo Van Den Heever: Vuma 30 Mbps
until 30 September, Carfone 50 Mbps from 1 October -- "choose partner from
1 October and new service 1 October", a new service record, and "System
should bill what his service will be 1 October".

The move is three bookings made ahead of time:
  * the customer's partner changes on the 1st (Customer.pending_partner*);
  * the Vuma service ends on the 1st (Service.end_date, first day WITHOUT
    service -- billing.cancellations, unchanged);
  * the Carfone service starts itself on the 1st (Pending + activate_on).

"Today" is pinned so these do not depend on the calendar.
"""
import datetime
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from customers.models import Customer, Partner
from customers.serializers import CustomerSerializer

from . import recurring
from .cancellations import apply_due_cancellations
from .invoice_lines import billable_services
from .models import BillingPreviewSnapshot, CustomerBillingConfig, Invoice, Service, Tariff
from .scheduled_changes import apply_due_scheduled_changes
from .serializers import ServiceSerializer

TODAY = datetime.date(2026, 9, 24)
MOVE = datetime.date(2026, 10, 1)
User = get_user_model()


class _Clock:
    def setUp(self):
        self.today = TODAY
        patcher = mock.patch("django.utils.timezone.localdate", side_effect=lambda *a, **k: self.today)
        patcher.start()
        self.addCleanup(patcher.stop)


class _Christo(_Clock):
    def setUp(self):
        super().setUp()
        self.vuma = Partner.objects.create(name="Vumatel Fibre")
        self.carfone = Partner.objects.create(name="Carfone Fibre")
        self.vuma_30 = Tariff.objects.create(name="Vuma BF 30/30 Mbps", price=Decimal("449.00"),
                                             tax_rate_pct=Decimal("15.00"), partner=self.vuma)
        self.carfone_50 = Tariff.objects.create(name="Carfone 50 Mbps", price=Decimal("599.00"),
                                                tax_rate_pct=Decimal("15.00"), partner=self.carfone)
        self.christo = Customer.objects.create(
            full_name="Christo Van Den Heever", customer_id="CUS-001603", email="christo@example.com",
            status=Customer.Status.ACTIVE, partner=self.vuma,
        )
        self.vuma_line = Service.objects.create(
            customer=self.christo, tariff=self.vuma_30, status=Service.Status.ACTIVE,
            start_date=datetime.date(2026, 9, 24), radius_username="christo_vuma",
        )
        config = CustomerBillingConfig.for_customer(self.christo)
        config.billing_enabled = True
        config.auto_create_invoices = True
        config.billing_day = 1
        config.next_billing_date = None
        config.save()

    def _book_the_move(self):
        """What staff do on the customer page."""
        Customer.objects.filter(pk=self.christo.pk).update(pending_partner=self.carfone, pending_partner_date=MOVE)
        self.christo.refresh_from_db()
        Service.objects.filter(pk=self.vuma_line.pk).update(end_date=MOVE)
        self.carfone_line = Service.objects.create(
            customer=self.christo, tariff=self.carfone_50, status=Service.Status.PENDING,
            start_date=MOVE, activate_on=MOVE, radius_username="christo_carfone",
        )

    def _approve(self, run_date=MOVE):
        from .preview_export import project_run, projection_digest

        return BillingPreviewSnapshot.objects.create(
            run_date=run_date, partner_ids=[], fingerprint=projection_digest(project_run(run_date)),
            counts={}, grand_total=Decimal("0"), sheets={}, approved_at=timezone.now(),
        )


class BillableServicesTests(_Christo, TestCase):
    def test_without_a_move_the_vuma_line_is_billed(self):
        self.assertEqual([s.pk for s in billable_services(self.christo, MOVE)], [self.vuma_line.pk])

    def test_with_the_move_booked_only_the_carfone_line_is_billed_on_the_1st(self):
        self._book_the_move()
        billed = billable_services(self.christo, MOVE)
        self.assertEqual([s.pk for s in billed], [self.carfone_line.pk])

    def test_before_the_move_date_the_vuma_line_is_still_billed(self):
        self._book_the_move()
        billed = billable_services(self.christo, datetime.date(2026, 9, 30))
        self.assertEqual([s.pk for s in billed], [self.vuma_line.pk])

    def test_a_pending_service_with_no_start_date_is_not_billed(self):
        Service.objects.create(customer=self.christo, tariff=self.vuma_30, status=Service.Status.PENDING,
                               radius_username="christo_install")
        self.assertEqual([s.pk for s in billable_services(self.christo, MOVE)], [self.vuma_line.pk])

    def test_a_booked_plan_change_is_billed_at_the_new_plan_from_its_date(self):
        other = Tariff.objects.create(name="Vuma BF 50/50", price=Decimal("549.00"),
                                      tax_rate_pct=Decimal("15.00"), partner=self.vuma)
        Service.objects.filter(pk=self.vuma_line.pk).update(pending_tariff=other, pending_tariff_date=MOVE)
        from .invoice_lines import plan_invoice_lines

        on_the_day = plan_invoice_lines(billable_services(self.christo, MOVE), MOVE)
        day_before = plan_invoice_lines(billable_services(self.christo, datetime.date(2026, 9, 30)),
                                        datetime.date(2026, 9, 30))
        self.assertEqual(on_the_day[0]["unit_price"], Decimal("549.00"))
        self.assertEqual(day_before[0]["unit_price"], Decimal("449.00"))
        self.vuma_line.refresh_from_db()
        self.assertEqual(self.vuma_line.tariff_id, self.vuma_30.pk, "billing changed the service row")


class ChristoEndToEndTests(_Christo, TestCase):
    def test_early_invoice_bills_carfone_and_the_1st_moves_him_cleanly(self):
        self._book_the_move()
        self._approve()

        # 24 Sep: the 1 Oct invoices issued early (#19).
        with self.captureOnCommitCallbacks(execute=True):
            recurring.run_recurring_billing(MOVE, commit=True, early=True)
        invoice = Invoice.objects.get(customer=self.christo)
        lines = list(invoice.items.values_list("description", "unit_price"))
        self.assertEqual(len(lines), 1)
        self.assertIn("Carfone 50 Mbps", lines[0][0])
        self.assertEqual(lines[0][1], Decimal("599.0000"))
        self.assertEqual(invoice.date_issued, MOVE)

        # 1 Oct, 04:15: the tariff-change cron applies the move and the start.
        self.today = MOVE
        out = StringIO()
        call_command("apply_tariff_changes", stdout=out)
        self.assertIn("partner Vumatel Fibre -> Carfone Fibre", out.getvalue())
        self.assertIn("started", out.getvalue())
        self.christo.refresh_from_db()
        self.carfone_line.refresh_from_db()
        self.assertEqual(self.christo.partner_id, self.carfone.pk)
        self.assertIsNone(self.christo.pending_partner_id)
        self.assertEqual(self.carfone_line.status, Service.Status.ACTIVE)
        self.assertIsNone(self.carfone_line.activate_on)

        # 04:30: the cancellations cron ends the Vuma line; he stays a customer.
        apply_due_cancellations(as_of=MOVE, commit=True)
        self.vuma_line.refresh_from_db()
        self.christo.refresh_from_db()
        self.assertEqual(self.vuma_line.status, Service.Status.TERMINATED)
        self.assertEqual(self.christo.status, Customer.Status.ACTIVE)

        # 15:00: the normal run bills nobody twice.
        with self.captureOnCommitCallbacks(execute=True):
            result = recurring.run_recurring_billing(MOVE, commit=True)
        self.assertEqual(result["counts"]["invoices_created"], 0)
        self.assertEqual(Invoice.objects.filter(customer=self.christo).count(), 1)

    def test_an_approval_taken_before_the_day_survives_the_morning_crons(self):
        """Billing as at the date means the 04:15/04:30 crons no longer make a
        preview approved beforehand go stale."""
        self._book_the_move()
        self._approve()
        self.today = MOVE
        call_command("apply_tariff_changes", stdout=StringIO())
        apply_due_cancellations(as_of=MOVE, commit=True)
        self.assertTrue(recurring.approval_for(MOVE)["ok"])

    def test_a_service_is_not_started_for_a_suspended_customer(self):
        self._book_the_move()
        Customer.objects.filter(pk=self.christo.pk).update(status=Customer.Status.SUSPENDED)
        result = apply_due_scheduled_changes(as_of=MOVE)
        self.assertEqual(result["started"], [])
        self.assertEqual(len(result["skipped"]), 1)
        self.carfone_line.refresh_from_db()
        self.assertEqual(self.carfone_line.status, Service.Status.PENDING)
        self.assertEqual(self.carfone_line.activate_on, MOVE)

    def test_nothing_happens_before_the_date(self):
        self._book_the_move()
        result = apply_due_scheduled_changes(as_of=datetime.date(2026, 9, 30))
        self.assertEqual((result["moved"], result["started"]), ([], []))


class ServiceRulesTests(_Christo, TestCase):
    def _service(self, **data):
        payload = {"customer": self.christo.pk, "tariff": self.carfone_50.pk,
                   "radius_connection_type": "ovpn", **data}
        return ServiceSerializer(data=payload)

    def _book_partner_only(self):
        Customer.objects.filter(pk=self.christo.pk).update(pending_partner=self.carfone, pending_partner_date=MOVE)
        self.christo.refresh_from_db()

    def test_another_partners_plan_is_refused_without_a_booked_move(self):
        s = self._service(status="pending", activate_on=MOVE.isoformat())
        self.assertFalse(s.is_valid())
        self.assertIn("tariff", s.errors)

    def test_the_booked_partners_plan_is_allowed_from_the_move_date(self):
        self._book_partner_only()
        s = self._service(status="pending", activate_on=MOVE.isoformat(), start_date=MOVE.isoformat())
        self.assertTrue(s.is_valid(), s.errors)

    def test_but_not_before_the_move_date(self):
        self._book_partner_only()
        s = self._service(status="pending", activate_on="2026-09-30")
        self.assertFalse(s.is_valid())
        self.assertIn("tariff", s.errors)

    def test_and_not_as_an_active_service_now(self):
        self._book_partner_only()
        s = self._service(status="active")
        self.assertFalse(s.is_valid())
        self.assertIn("tariff", s.errors)

    def test_activate_on_needs_pending_and_a_future_date(self):
        s = ServiceSerializer(data={"customer": self.christo.pk, "tariff": self.vuma_30.pk,
                                    "radius_connection_type": "ovpn", "status": "active",
                                    "activate_on": MOVE.isoformat()})
        self.assertFalse(s.is_valid())
        self.assertIn("activate_on", s.errors)
        s = ServiceSerializer(data={"customer": self.christo.pk, "tariff": self.vuma_30.pk,
                                    "radius_connection_type": "ovpn", "status": "pending",
                                    "activate_on": TODAY.isoformat()})
        self.assertFalse(s.is_valid())
        self.assertIn("activate_on", s.errors)

    def test_the_old_service_stays_editable_after_the_partner_moved(self):
        Customer.objects.filter(pk=self.christo.pk).update(partner=self.carfone)
        self.christo.refresh_from_db()
        s = ServiceSerializer(self.vuma_line, data={"end_date": MOVE.isoformat()}, partial=True)
        self.assertTrue(s.is_valid(), s.errors)

    def test_the_plan_dropdown_offers_the_booked_partners_plans(self):
        from .views import TariffViewSet

        staff = User.objects.create_user(username="mv_admin", password="x", role="admin")

        def names():
            request = APIRequestFactory().get("/tariffs/", {"for_customer": self.christo.pk})
            force_authenticate(request, user=staff)
            data = TariffViewSet.as_view({"get": "list"})(request).data
            rows = data["results"] if isinstance(data, dict) else data
            return {r["name"] for r in rows}

        self.assertEqual(names(), {"Vuma BF 30/30 Mbps"})
        self._book_partner_only()
        self.assertEqual(names(), {"Vuma BF 30/30 Mbps", "Carfone 50 Mbps"})


class CustomerMoveRulesTests(_Christo, TestCase):
    def _patch(self, **data):
        return CustomerSerializer(self.christo, data=data, partial=True)

    def test_a_move_is_saved(self):
        s = self._patch(pending_partner=self.carfone.pk, pending_partner_date=MOVE.isoformat())
        self.assertTrue(s.is_valid(), s.errors)
        s.save()
        self.christo.refresh_from_db()
        self.assertEqual((self.christo.pending_partner_id, self.christo.pending_partner_date),
                         (self.carfone.pk, MOVE))

    def test_needs_both_halves(self):
        s = self._patch(pending_partner=self.carfone.pk)
        self.assertFalse(s.is_valid())
        self.assertIn("pending_partner_date", s.errors)
        s = self._patch(pending_partner_date=MOVE.isoformat())
        self.assertFalse(s.is_valid())
        self.assertIn("pending_partner", s.errors)

    def test_refuses_the_current_partner_and_today(self):
        s = self._patch(pending_partner=self.vuma.pk, pending_partner_date=MOVE.isoformat())
        self.assertFalse(s.is_valid())
        self.assertIn("pending_partner", s.errors)
        s = self._patch(pending_partner=self.carfone.pk, pending_partner_date=TODAY.isoformat())
        self.assertFalse(s.is_valid())
        self.assertIn("pending_partner_date", s.errors)

    def test_clearing_a_booking(self):
        Customer.objects.filter(pk=self.christo.pk).update(pending_partner=self.carfone, pending_partner_date=MOVE)
        self.christo.refresh_from_db()
        s = self._patch(pending_partner=None, pending_partner_date=None)
        self.assertTrue(s.is_valid(), s.errors)
