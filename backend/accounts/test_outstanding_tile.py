"""Outstanding balance = what clients actually owe (Rudolph, 6 Oct 2026:
"Outstanding balance should only show the unpaid balance of all clients")."""
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from billing.models import Invoice
from customers.models import Customer

User = get_user_model()


class OutstandingTileTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username="boss", password="x", role="admin")

    def _summary(self):
        from accounts.views import DashboardSummaryView

        request = APIRequestFactory().get("/x")
        force_authenticate(request, user=self.staff)
        return DashboardSummaryView.as_view()(request).data

    def test_sums_client_balances_in_debit_only(self):
        half_paid = Customer.objects.create(full_name="Half paid", balance=Decimal("400.00"))
        Invoice.objects.filter(pk=Invoice.objects.create(customer=half_paid, status="unpaid", date_due=date.today()).pk).update(
            total=Decimal("1000.00"), paid_amount=Decimal("600.00"))
        Customer.objects.create(full_name="Owes", balance=Decimal("250.50"))
        Customer.objects.create(full_name="In credit", balance=Decimal("-300.00"))
        Customer.objects.create(full_name="Square", balance=Decimal("0.00"))
        data = self._summary()
        self.assertEqual(Decimal(str(data["outstanding_balance"])), Decimal("650.50"))
        self.assertEqual(data["customers_owing"], 2)
        self.assertEqual(data["invoices_unpaid"], 1)

    def test_nothing_owed(self):
        Customer.objects.create(full_name="In credit", balance=Decimal("-10.00"))
        data = self._summary()
        self.assertEqual(Decimal(str(data["outstanding_balance"])), Decimal("0"))
        self.assertEqual(data["customers_owing"], 0)

    def test_overdue_is_counted_by_due_date(self):
        from datetime import timedelta

        c = Customer.objects.create(full_name="Late", balance=Decimal("200.00"))
        for days in (-40, -1, 5):
            Invoice.objects.create(customer=c, status="unpaid", date_due=date.today() + timedelta(days=days))
        Invoice.objects.create(customer=c, status="paid", date_due=date.today() - timedelta(days=60))
        data = self._summary()
        self.assertEqual(data["invoices_unpaid"], 3)
        self.assertEqual(data["invoices_overdue"], 2)
