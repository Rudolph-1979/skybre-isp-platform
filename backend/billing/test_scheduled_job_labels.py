"""apply_tariff_changes names itself in the audit trail (package #23),
the same way apply_cancellations and apply_speed_policies already do.

Without this, a partner move or tariff change applied overnight by the
04:15 SAST cron wrote a History-tab row with a blank "who" -- unlike a
cancellation, which already says "apply_cancellations (scheduled job)".
Rudolph, 24 Sep: "For reporting it seems we can use the history tab to see
the product changes?" -- yes, but only if the automated ones say who/what
made them too.
"""
import datetime
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase

from audit.models import AuditEvent
from customers.models import Customer, Partner

from .models import Service, Tariff

TODAY = datetime.date(2026, 9, 24)
MOVE = datetime.date(2026, 10, 1)


class ScheduledJobNamesItselfTests(TestCase):
    def setUp(self):
        # Pinned, like billing.test_scheduled_moves._Clock -- the command
        # refuses a --as-of/today after the booked date has not yet arrived,
        # so "today" has to actually reach MOVE for these to apply at all.
        self.today = TODAY
        patcher = mock.patch("django.utils.timezone.localdate", side_effect=lambda *a, **k: self.today)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.vuma = Partner.objects.create(name="Vumatel Fibre")
        self.carfone = Partner.objects.create(name="Carfone Fibre")
        self.tariff = Tariff.objects.create(name="Vuma BF 30/30 Mbps", price=Decimal("449.00"),
                                            tax_rate_pct=Decimal("15.00"), partner=self.vuma)
        self.new_tariff = Tariff.objects.create(name="Vuma BF 60/60 Mbps", price=Decimal("699.00"),
                                                tax_rate_pct=Decimal("15.00"), partner=self.vuma)
        self.customer = Customer.objects.create(
            full_name="Jaco Test", customer_id="CUS-007701", email="jaco@example.com",
            status=Customer.Status.ACTIVE, partner=self.vuma,
            pending_partner=self.carfone, pending_partner_date=MOVE,
        )
        self.service = Service.objects.create(
            customer=self.customer, tariff=self.tariff, status=Service.Status.ACTIVE,
            pending_tariff=self.new_tariff, pending_tariff_date=MOVE,
        )

    def test_a_booked_partner_move_names_the_job(self):
        AuditEvent.objects.all().delete()
        self.today = MOVE
        call_command("apply_tariff_changes", stdout=StringIO())
        event = AuditEvent.objects.filter(target_type="customers.Customer", action="updated").first()
        self.assertIsNotNone(event)
        self.assertIsNone(event.actor)
        self.assertIn("apply_tariff_changes", event.actor_label)

    def test_a_booked_tariff_change_names_the_job(self):
        AuditEvent.objects.all().delete()
        self.today = MOVE
        call_command("apply_tariff_changes", stdout=StringIO())
        event = AuditEvent.objects.filter(target_type="billing.Service", action="updated").first()
        self.assertIsNotNone(event)
        self.assertIsNone(event.actor)
        self.assertIn("apply_tariff_changes", event.actor_label)

    def test_a_dry_run_writes_no_audit_row(self):
        AuditEvent.objects.all().delete()
        self.today = MOVE
        call_command("apply_tariff_changes", "--dry-run", stdout=StringIO())
        self.assertEqual(AuditEvent.objects.count(), 0)
