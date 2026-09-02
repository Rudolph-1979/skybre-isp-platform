"""Dashboard counts that drive the tiles, and the lists they click through to."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from customers.models import Customer
from customers.views import CustomerViewSet

User = get_user_model()


class BadDebtTileTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username="boss", password="x", role="admin")
        self.factory = APIRequestFactory()
        Customer.objects.create(full_name="Owes A", email="a@x.com",
                                status=Customer.Status.BAD_DEBT, balance=Decimal("1200.50"))
        Customer.objects.create(full_name="Owes B", email="b@x.com",
                                status=Customer.Status.BAD_DEBT, balance=Decimal("800.00"))
        Customer.objects.create(full_name="Fine", email="c@x.com",
                                status=Customer.Status.ACTIVE, balance=Decimal("50.00"))

    def _summary(self):
        from accounts.views import DashboardSummaryView
        request = self.factory.get("/x")
        force_authenticate(request, user=self.staff)
        return DashboardSummaryView.as_view()(request).data

    def test_the_count_and_the_money(self):
        data = self._summary()
        self.assertEqual(data["customers_bad_debt"], 2)
        self.assertEqual(Decimal(str(data["customers_bad_debt_value"])), Decimal("2000.50"))

    def test_an_active_customers_balance_is_not_counted_as_written_off(self):
        self.assertEqual(Decimal(str(self._summary()["customers_bad_debt_value"])), Decimal("2000.50"))

    def test_the_tile_links_to_a_list_that_actually_filters(self):
        """The tile goes to /admin/customers?status=bad_debt; this is the
        request that page then makes."""
        request = self.factory.get("/x", {"status": "bad_debt"})
        force_authenticate(request, user=self.staff)
        response = CustomerViewSet.as_view({"get": "list"})(request)
        self.assertEqual(response.status_code, 200)
        names = sorted(r["full_name"] for r in response.data["results"])
        self.assertEqual(names, ["Owes A", "Owes B"])

    def test_cancelled_and_bad_debt_are_counted_separately(self):
        Customer.objects.create(full_name="Left", email="d@x.com", status=Customer.Status.INACTIVE)
        data = self._summary()
        self.assertEqual(data["customers_bad_debt"], 2)
        self.assertEqual(data["customers_cancelled"], 1)
