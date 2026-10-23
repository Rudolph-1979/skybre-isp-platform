"""An approved credit reconnects a customer it clears, same as a payment.

Found live 1 Oct 2026: a credit applied to the overdue invoice settled it,
but the account stayed suspended -- reconnect_customer only ever ran from
the Payment signal and the daily sweep.
"""
import datetime
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.testing import ALL_SECTIONS
from customers.models import Customer

from .models import CreditRequest, CustomerBillingConfig, Invoice, Service, Tariff

User = get_user_model()


class CreditReconnectsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.manager = User.objects.create_user(
            username="manager-cr", password="pw-for-tests", role=User.Role.MANAGEMENT,
            allowed_sections=ALL_SECTIONS,
        )
        tariff = Tariff.objects.create(
            name="Home 10", price=Decimal("433.55"), tax_rate_pct=Decimal("15.00"),
        )
        self.customer = Customer.objects.create(
            full_name="Jay Test", customer_id="CUS-009300",
            email="jay@example.com", status=Customer.Status.ACTIVE,
        )
        config = CustomerBillingConfig.for_customer(self.customer)
        config.blocking_period_days = 4
        config.save()
        self.service = Service.objects.create(
            customer=self.customer, tariff=tariff,
            status=Service.Status.ACTIVE, start_date=datetime.date(2026, 8, 1),
        )
        Service.objects.filter(pk=self.service.pk).update(
            status=Service.Status.SUSPENDED, suspended_for_nonpayment=True
        )
        self.invoice = Invoice.objects.create(
            customer=self.customer, status=Invoice.Status.UNPAID,
            date_due=datetime.date.today() - datetime.timedelta(days=28),
        )
        Invoice.objects.filter(pk=self.invoice.pk).update(
            total=Decimal("433.55"), paid_amount=Decimal("420.00")
        )
        Customer.objects.filter(pk=self.customer.pk).update(balance=Decimal("402.50"))

    def _approve(self, amount, invoice=True):
        credit = CreditRequest.objects.create(
            customer=self.customer, amount=Decimal(amount), reason="test",
            invoice=self.invoice if invoice else None,
        )
        self.client.force_authenticate(self.manager)
        with self.captureOnCommitCallbacks(execute=True):
            res = self.client.post(f"/api/credit-requests/{credit.id}/approve/")
        self.assertEqual(res.status_code, 200, res.data)
        self.service.refresh_from_db()

    def test_a_credit_that_settles_the_overdue_invoice_reconnects(self):
        self._approve("13.55")
        self.assertEqual(self.service.status, Service.Status.ACTIVE)

    def test_a_credit_that_leaves_it_overdue_keeps_them_off(self):
        self._approve("5.00")
        self.assertEqual(self.service.status, Service.Status.SUSPENDED)

    def test_a_failed_reconnection_does_not_break_the_approval(self):
        with mock.patch("billing.reactivation.reactivate", side_effect=RuntimeError("radius down")):
            self._approve("13.55")
        self.assertTrue(CreditRequest.objects.filter(status=CreditRequest.Status.APPROVED).exists())
