"""Customer Overview "Payment" tile (Rudolph, 7 Oct 2026: "a tile to show
the client payment option and when he will be blocked next")."""
import datetime
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from billing.models import CustomerBillingConfig, Invoice, PaymentMethod, Service, Tariff
from billing.recurring import upcoming_blocks
from customers.models import Customer


class PaymentSummaryTests(TestCase):
    def setUp(self):
        admin = User.objects.create_user(username="boss", password="x", role="admin")
        self.api = APIClient()
        self.api.force_authenticate(admin)
        self.customer = Customer.objects.create(full_name="Danny Du Plessis", customer_id="SMF839")
        tariff = Tariff.objects.create(name="10 Mbps", price=Decimal("330"))
        Service.objects.create(customer=self.customer, tariff=tariff, status="active")
        self.config = CustomerBillingConfig.for_customer(self.customer)
        self.config.payment_method = PaymentMethod.objects.create(name="EFT 1st")
        self.config.billing_enabled = True
        self.config.blocking_period_days = 7
        self.config.save()

    def get(self):
        res = self.api.get(f"/api/customers/{self.customer.pk}/payment-summary/")
        self.assertEqual(res.status_code, 200, res.data)
        return res.data

    def test_nothing_owing(self):
        data = self.get()
        self.assertEqual(data["payment_method"], "EFT 1st")
        self.assertIsNone(data["block_date"])
        self.assertIn("Nothing owing", data["block_note"])

    def test_block_date_matches_the_dashboard_rule(self):
        due = timezone.localdate() + datetime.timedelta(days=3)
        Invoice.objects.create(customer=self.customer, status=Invoice.Status.UNPAID, date_due=due,
                               total=Decimal("330"))
        Customer.objects.filter(pk=self.customer.pk).update(balance=Decimal("330"))
        data = self.get()
        expected = upcoming_blocks(timezone.localdate() + datetime.timedelta(days=1), horizon_days=400,
                                   customers=Customer.objects.filter(pk=self.customer.pk))[0]["block_date"]
        self.assertEqual(data["block_date"], expected)
        self.assertGreaterEqual(data["days_until_block"], 10)

    def test_billing_off(self):
        self.config.billing_enabled = False
        self.config.save()
        self.assertIn("billing is off", self.get()["block_note"])
