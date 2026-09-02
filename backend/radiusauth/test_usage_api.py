"""The three surfaces: staff per-customer, the no-login link, and the report."""
import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from billing.models import Service, Tariff
from customers.models import Customer, Partner
from radiusauth import usage
from radiusauth.views import CustomerUsageView, PublicUsageView, UsageReportView

User = get_user_model()
MB = 1024 * 1024


def at(y, m, d, h=0):
    return timezone.make_aware(datetime.datetime(y, m, d, h), timezone.get_current_timezone())


class UsageApiTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username="boss", password="x", role="admin")
        self.tariff = Tariff.objects.create(
            name="Capped", price=Decimal("1"), speed_download_kbps=4096,
            speed_upload_kbps=4096, data_cap_gb=1,
        )
        self.customer = Customer.objects.create(full_name="Usage Ursula", email="u@x.com")
        Service.objects.create(
            customer=self.customer, tariff=self.tariff, status=Service.Status.ACTIVE,
            start_date="2026-08-01", radius_username="ursula",
        )
        usage.bank_usage("ursula", at(2026, 8, 26, 9), download=300 * MB, upload=20 * MB)
        usage.bank_usage("ursula", at(2026, 8, 24, 9), download=100 * MB, upload=10 * MB)

    def _get(self, view, path, params, user=None, **kwargs):
        request = APIRequestFactory().get(path, params)
        if user:
            force_authenticate(request, user=user)
        return view.as_view()(request, **kwargs)

    def test_staff_view_returns_a_series(self):
        r = self._get(CustomerUsageView, "/x", {"period": "day", "date": "2026-08-26"},
                      user=self.staff, pk=self.customer.pk)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.data["series"]["points"]), 24)
        self.assertEqual(r.data["series"]["download_bytes"], 300 * MB)
        self.assertIsNotNone(r.data["measuring_since"])

    def test_week_and_year_are_available(self):
        week = self._get(CustomerUsageView, "/x", {"period": "week", "date": "2026-08-26"},
                         user=self.staff, pk=self.customer.pk)
        self.assertEqual(week.data["series"]["total_bytes"], 430 * MB)
        year = self._get(CustomerUsageView, "/x", {"period": "year", "date": "2026-08-26"},
                         user=self.staff, pk=self.customer.pk)
        self.assertEqual(len(year.data["series"]["points"]), 12)

    def test_omitting_period_keeps_the_old_response(self):
        """The month-to-date figures the page has always shown must survive."""
        r = self._get(CustomerUsageView, "/x", {}, user=self.staff, pk=self.customer.pk)
        self.assertNotIn("series", r.data)
        self.assertIn("total_bytes", r.data)

    def test_a_bad_period_is_a_400_not_a_500(self):
        r = self._get(CustomerUsageView, "/x", {"period": "fortnight"},
                      user=self.staff, pk=self.customer.pk)
        self.assertEqual(r.status_code, 400)

    def test_a_bad_date_is_a_400(self):
        r = self._get(CustomerUsageView, "/x", {"period": "day", "date": "26-08-2026"},
                      user=self.staff, pk=self.customer.pk)
        self.assertEqual(r.status_code, 400)

    def test_public_link_gets_the_series_but_not_the_radius_username(self):
        r = self._get(PublicUsageView, "/x", {"period": "month", "date": "2026-08-26"},
                      token=str(self.customer.usage_token))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["series"]["total_bytes"], 430 * MB)
        self.assertNotIn("customer_id", r.data)
        for session in r.data.get("live_sessions", []):
            self.assertNotIn("username", session)

    def test_public_link_rejects_a_bad_token(self):
        self.assertEqual(self._get(PublicUsageView, "/x", {}, token="nope").status_code, 404)

    # --- the staff-wide report -------------------------------------------

    def test_report_ranks_heaviest_first(self):
        other = Customer.objects.create(full_name="Light Larry", email="l@x.com")
        Service.objects.create(customer=other, tariff=self.tariff, status=Service.Status.ACTIVE,
                               start_date="2026-08-01", radius_username="larry")
        usage.bank_usage("larry", at(2026, 8, 26, 9), download=5 * MB, upload=0)

        r = self._get(UsageReportView, "/x", {"period": "month", "date": "2026-08-26"}, user=self.staff)
        names = [row["full_name"] for row in r.data["results"]]
        self.assertEqual(names, ["Usage Ursula", "Light Larry"])

    def test_report_shows_cap_usage(self):
        r = self._get(UsageReportView, "/x", {"period": "month", "date": "2026-08-26"}, user=self.staff)
        row = r.data["results"][0]
        self.assertEqual(row["cap_bytes"], 1024 ** 3)
        self.assertAlmostEqual(row["cap_used_pct"], 42.0, places=0)

    def test_report_respects_partner_scoping(self):
        mine = Partner.objects.create(name="Mine")
        theirs = Partner.objects.create(name="Theirs")
        self.customer.partner = theirs
        self.customer.save()
        scoped = User.objects.create_user(username="reseller", password="x", role="support")
        scoped.allowed_partners = [mine.id]
        scoped.save()
        r = self._get(UsageReportView, "/x", {"period": "month", "date": "2026-08-26"}, user=scoped)
        self.assertEqual(r.data["results"], [])

    def test_report_is_staff_only(self):
        customer_user = User.objects.create_user(username="cust", password="x", role="customer")
        self.assertEqual(self._get(UsageReportView, "/x", {}, user=customer_user).status_code, 404)

    def test_report_query_count_is_flat(self):
        """Two queries regardless of how many customers -- the naive version is
        one per customer and turns a 500-row report into 500 round trips."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        for i in range(12):
            c = Customer.objects.create(full_name=f"Bulk {i}", email=f"b{i}@x.com")
            Service.objects.create(customer=c, tariff=self.tariff, status=Service.Status.ACTIVE,
                                   start_date="2026-08-01", radius_username=f"bulk{i}")
            usage.bank_usage(f"bulk{i}", at(2026, 8, 26, 9), download=MB, upload=0)

        with CaptureQueriesContext(connection) as ctx:
            usage.usage_report(list(Customer.objects.all()), "month", datetime.date(2026, 8, 26))
        self.assertLessEqual(len(ctx), 4, f"{len(ctx)} queries: the report is doing per-customer work")
