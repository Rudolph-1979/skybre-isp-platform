"""A suspended LINE shows beside the customer's status (display only).

The billing run suspends a customer's services for non-payment and leaves
Customer.status alone (billing.recurring._process_blocking), so a customer
whose only line was off read "Active" on every screen. Rudolph's decision,
2026-09-23: show it, don't change the status. These tests pin:

  * the customer API reports suspended_lines / suspended_for_nonpayment_lines
    on list and detail, and leaves the status as it was;
  * the Customers list filter ?line_suspended=any|nonpayment returns exactly
    those customers, and the dashboard tile counts the same set;
  * the counts cost a constant number of queries per page, not one per row;
  * the service API exposes suspended_for_nonpayment and cannot write it.

Statuses and flags are set with .update() on purpose: the point here is
what the screens SHOW for a state, not how the state was reached (that is
covered by billing's own tests), and .update() keeps the signals and any
RADIUS side effects out of it.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIRequestFactory, force_authenticate

from billing.models import Service, Tariff
from billing.serializers import ServiceSerializer
from customers.models import Customer
from customers.serializers import CustomerSerializer
from customers.views import CustomerViewSet

User = get_user_model()


class LineSuspensionTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username="ls_admin", password="x", role="admin")
        self.factory = APIRequestFactory()
        self.tariff = Tariff.objects.create(
            name="LS 20", price=Decimal("500.00"), speed_download_kbps=20480, speed_upload_kbps=10240,
        )
        # Billing cut her off; her own status still says Active.
        self.unpaid = self._customer("Unpaid Una")
        self.unpaid_line = self._service(self.unpaid, "una", Service.Status.SUSPENDED, nonpayment=True)
        # Staff took his line down themselves.
        self.staff_off = self._customer("Manual Manie")
        self._service(self.staff_off, "manie", Service.Status.SUSPENDED)
        # Two lines: one cut for non-payment, one still up.
        self.mixed = self._customer("Mixed Mia")
        self._service(self.mixed, "mia1", Service.Status.SUSPENDED, nonpayment=True)
        self._service(self.mixed, "mia2", Service.Status.ACTIVE)
        # Nothing suspended.
        self.fine = self._customer("Fine Fanie")
        self._service(self.fine, "fanie", Service.Status.ACTIVE)
        # A stale flag on a line that is back on must not count.
        self.back_on = self._customer("Back Bennie")
        self._service(self.back_on, "bennie", Service.Status.ACTIVE, nonpayment=True)

    def _customer(self, name):
        slug = name.lower().replace(" ", "_")
        return Customer.objects.create(full_name=name, email=f"{slug}@example.com", status=Customer.Status.ACTIVE)

    def _service(self, customer, slug, status, nonpayment=False):
        service = Service.objects.create(
            customer=customer, tariff=self.tariff, status=Service.Status.ACTIVE,
            start_date="2026-01-01", radius_username=f"ls_{slug}",
        )
        Service.objects.filter(pk=service.pk).update(status=status, suspended_for_nonpayment=nonpayment)
        return service

    def _list(self, **params):
        request = self.factory.get("/customers/", params)
        force_authenticate(request, user=self.staff)
        return CustomerViewSet.as_view({"get": "list"})(request).data

    def _rows(self, **params):
        return {r["full_name"]: r for r in self._list(**params)["results"]}

    # -- what the customer API reports ------------------------------------

    def test_list_reports_suspended_lines_and_why(self):
        rows = self._rows()
        self.assertEqual((rows["Unpaid Una"]["suspended_lines"], rows["Unpaid Una"]["suspended_for_nonpayment_lines"]), (1, 1))
        self.assertEqual((rows["Manual Manie"]["suspended_lines"], rows["Manual Manie"]["suspended_for_nonpayment_lines"]), (1, 0))
        self.assertEqual((rows["Mixed Mia"]["suspended_lines"], rows["Mixed Mia"]["suspended_for_nonpayment_lines"]), (1, 1))
        self.assertEqual((rows["Fine Fanie"]["suspended_lines"], rows["Fine Fanie"]["suspended_for_nonpayment_lines"]), (0, 0))
        self.assertEqual((rows["Back Bennie"]["suspended_lines"], rows["Back Bennie"]["suspended_for_nonpayment_lines"]), (0, 0))

    def test_the_status_is_shown_as_it_is_not_changed(self):
        """Display only: the customer still reads Active, in the API and in
        the database."""
        self.assertEqual(self._rows()["Unpaid Una"]["status"], "active")
        self.unpaid.refresh_from_db()
        self.assertEqual(self.unpaid.status, Customer.Status.ACTIVE)
        self.unpaid_line.refresh_from_db()
        self.assertEqual(self.unpaid_line.status, Service.Status.SUSPENDED)
        self.assertTrue(self.unpaid_line.suspended_for_nonpayment)

    def test_detail_reports_them_too(self):
        request = self.factory.get(f"/customers/{self.unpaid.pk}/")
        force_authenticate(request, user=self.staff)
        data = CustomerViewSet.as_view({"get": "retrieve"})(request, pk=self.unpaid.pk).data
        self.assertEqual(data["suspended_lines"], 1)
        self.assertEqual(data["suspended_for_nonpayment_lines"], 1)

    def test_without_the_map_the_serializer_says_nothing_rather_than_querying(self):
        data = CustomerSerializer(self.unpaid).data
        self.assertIsNone(data["suspended_lines"])
        self.assertIsNone(data["suspended_for_nonpayment_lines"])

    def test_constant_queries_per_page(self):
        with CaptureQueriesContext(connection) as small:
            self._list(page_size=2)
        with CaptureQueriesContext(connection) as large:
            self._list(page_size=50)
        self.assertEqual(len(small), len(large), "the suspended-line counts cost a query per customer")

    # -- the filter and the dashboard tile ---------------------------------

    def test_filter_nonpayment(self):
        self.assertEqual(set(self._rows(line_suspended="nonpayment")), {"Unpaid Una", "Mixed Mia"})

    def test_filter_any(self):
        self.assertEqual(set(self._rows(line_suspended="any")), {"Unpaid Una", "Manual Manie", "Mixed Mia"})

    def test_filter_ignores_nonsense(self):
        self.assertEqual(len(self._rows(line_suspended="banana")), 5)

    def test_filter_does_not_duplicate_a_customer_with_two_suspended_lines(self):
        self._service(self.unpaid, "una2", Service.Status.SUSPENDED, nonpayment=True)
        names = [r["full_name"] for r in self._list(line_suspended="nonpayment")["results"]]
        self.assertEqual(sorted(names), ["Mixed Mia", "Unpaid Una"])
        self.assertEqual(self._rows()["Unpaid Una"]["suspended_lines"], 2)

    def test_dashboard_counts_match_the_filter(self):
        from accounts.views import DashboardSummaryView

        request = self.factory.get("/x")
        force_authenticate(request, user=self.staff)
        data = DashboardSummaryView.as_view()(request).data
        self.assertEqual(data["customers_suspended_nonpayment"], 2)
        self.assertEqual(data["customers_with_suspended_lines"], 3)
        self.assertEqual(data["customers_suspended_nonpayment"], self._list(line_suspended="nonpayment")["count"])
        self.assertEqual(data["customers_with_suspended_lines"], self._list(line_suspended="any")["count"])

    # -- the service API ----------------------------------------------------

    def test_service_api_shows_the_flag(self):
        self.unpaid_line.refresh_from_db()
        self.assertIs(ServiceSerializer(self.unpaid_line).data["suspended_for_nonpayment"], True)

    def test_service_api_cannot_write_the_flag(self):
        """Only the blocking pass and billing.reactivation may set it --
        a PATCH that sends it must not get through validation."""
        fine_line = Service.objects.get(radius_username="ls_fanie")
        serializer = ServiceSerializer(fine_line, data={"suspended_for_nonpayment": True}, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertNotIn("suspended_for_nonpayment", serializer.validated_data)
