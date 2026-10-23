"""Payer rating on the Customers list (21 Oct 2026, Rudolph: "show if the
customer is a constant late payer or a constant early or on time payer" --
last 12 invoices, on time = on or before the due date)."""
import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from billing.models import CustomerBillingConfig, Invoice, Payment, PaymentAllocation
from billing.payer_rating import ratings
from customers.models import Customer

User = get_user_model()
D = datetime.date
TODAY = D(2026, 10, 21)


class PayerRatingTests(TestCase):
    def client_with(self, name, days_list, blocking=4, unpaid_overdue_days=None):
        c = Customer.objects.create(full_name=name, status=Customer.Status.ACTIVE)
        g = CustomerBillingConfig.for_customer(c)
        g.blocking_period_days = blocking
        g.save()
        for i, days in enumerate(days_list):
            due = D(2026, 1 + i, 1)
            inv = Invoice.objects.create(customer=c, status=Invoice.Status.PAID, date_due=due)
            Invoice.objects.filter(pk=inv.pk).update(total=Decimal("100.00"), paid_amount=Decimal("100.00"))
            pay = Payment.objects.create(customer=c, amount=Decimal("100.00"))
            when = timezone.make_aware(datetime.datetime.combine(due + datetime.timedelta(days=days), datetime.time(10)))
            Payment.objects.filter(pk=pay.pk).update(date=when)   # date is auto_now_add
            PaymentAllocation.objects.create(payment=pay, invoice=inv, amount=Decimal("100.00"))
        if unpaid_overdue_days is not None:
            inv = Invoice.objects.create(customer=c, status=Invoice.Status.UNPAID,
                                         date_due=TODAY - datetime.timedelta(days=unpaid_overdue_days))
            Invoice.objects.filter(pk=inv.pk).update(total=Decimal("100.00"))
        return c

    def code(self, c):
        return ratings([c.pk], today=TODAY)[c.pk]

    def test_categories(self):
        self.assertEqual(self.code(self.client_with("Early", [-3, -1, -5, 0]))["code"], "early")
        self.assertEqual(self.code(self.client_with("OnTime", [0, 0, -1, 2]))["code"], "on_time")
        self.assertEqual(self.code(self.client_with("Late", [2, 3, 1, 0]))["code"], "late")
        self.assertEqual(self.code(self.client_with("Chronic", [10, 12, 9, 1]))["code"], "chronic")
        self.assertEqual(self.code(self.client_with("New", [0, 0]))["code"], "new")

    def test_unpaid_overdue_counts_as_late(self):
        r = self.code(self.client_with("Owing", [0, 0], unpaid_overdue_days=20))
        self.assertEqual((r["counted"], r["late"]), (3, 1))
        self.assertIn("1 late", r["detail"])

    def test_only_last_12(self):
        c = self.client_with("Year", [5] * 12)   # Jan..Dec all late
        r = self.code(c)
        self.assertEqual(r["counted"], 12)        # paid ones count even if due later
        self.assertEqual(r["code"], "chronic")

    def test_list_column_and_filter(self):
        early = self.client_with("Early", [-3, -1, -5, 0])
        late = self.client_with("Late", [2, 3, 1, 0])
        api = APIClient()
        api.force_authenticate(User.objects.create_user(username="boss", password="x", role="admin"))
        res = api.get("/api/customers/?payer=late")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual([r["id"] for r in res.data["results"]], [late.pk])
        res = api.get(f"/api/customers/?payer=early,late&ordering=full_name")
        names = {r["full_name"]: r["payer_rating"]["code"] for r in res.data["results"]}
        self.assertEqual(names, {"Early": "early", "Late": "late"})
        self.assertIsNotNone(api.get(f"/api/customers/{early.pk}/").data["payer_rating"])
