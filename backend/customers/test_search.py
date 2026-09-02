from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.db import connection
from decimal import Decimal
from rest_framework.test import APIRequestFactory, force_authenticate
from django.contrib.auth import get_user_model

from customers.models import Customer, Partner
from customers.views import CustomerViewSet
from billing.models import Service, Tariff
from network.models import Device, IPPool, IPAddress

User = get_user_model()


class CustomerSearchTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username="tech1", password="x", role="admin", first_name="Ruan")
        self.partner = Partner.objects.create(name="Skybre Wireless")
        self.tariff = Tariff.objects.create(name="T", price=Decimal("1"), speed_download_kbps=4096, speed_upload_kbps=4096)
        self.device = Device.objects.create(name="Bench", ip_address="10.0.0.1")
        self.pool = IPPool.objects.create(name="Cust", network_cidr="102.23.154.0/24", category=IPPool.Category.CUSTOMER)
        self.addr = IPAddress.objects.create(pool=self.pool, address="102.23.154.3", status=IPAddress.Status.ASSIGNED)

        self.c1 = Customer.objects.create(full_name="Anton Everts", email="a@x.com", status="active",
                                          partner=self.partner, assigned_staff=self.staff, balance=Decimal("1250.00"))
        self.c2 = Customer.objects.create(full_name="Janine Erasmus", email="j@x.com", status="new")

        self.s1 = Service.objects.create(customer=self.c1, tariff=self.tariff, status="active",
                                         start_date="2026-08-01", radius_connection_type="pppoe",
                                         ip_assignment_mode="pool", ip_pool=self.pool,
                                         radius_username="anton_pppoe")
        self.addr.assigned_service = self.s1
        self.addr.save()
        self.s2 = Service.objects.create(customer=self.c2, tariff=self.tariff, status="active",
                                         start_date="2026-08-01", radius_connection_type="pppoe",
                                         ip_assignment_mode="manual", static_ip="197.80.1.9")

    def _list(self, **params):
        request = APIRequestFactory().get("/customers/", params)
        force_authenticate(request, user=self.staff)
        view = CustomerViewSet.as_view({"get": "list"})
        return view(request).data

    def test_public_ips_are_returned(self):
        rows = {r["full_name"]: r["public_ips"] for r in self._list()["results"]}
        self.assertEqual(rows["Anton Everts"], ["102.23.154.3"])
        self.assertEqual(rows["Janine Erasmus"], ["197.80.1.9"])

    def test_search_by_pool_ip(self):
        rows = self._list(search="102.23.154.3")["results"]
        self.assertEqual([r["full_name"] for r in rows], ["Anton Everts"])

    def test_search_by_static_ip(self):
        rows = self._list(search="197.80")["results"]
        self.assertEqual([r["full_name"] for r in rows], ["Janine Erasmus"])

    def test_search_by_partner(self):
        rows = self._list(search="Skybre Wireless")["results"]
        self.assertEqual([r["full_name"] for r in rows], ["Anton Everts"])

    def test_search_by_staff(self):
        rows = self._list(search="Ruan")["results"]
        self.assertEqual([r["full_name"] for r in rows], ["Anton Everts"])

    def test_search_by_status(self):
        rows = self._list(search="active")["results"]
        self.assertEqual([r["full_name"] for r in rows], ["Anton Everts"])

    def test_search_by_balance(self):
        rows = self._list(search="1250")["results"]
        self.assertEqual([r["full_name"] for r in rows], ["Anton Everts"])

    def test_search_by_radius_username(self):
        rows = self._list(search="anton_pppoe")["results"]
        self.assertEqual([r["full_name"] for r in rows], ["Anton Everts"])

    def test_no_duplicate_rows_when_a_customer_has_two_matching_services(self):
        Service.objects.create(customer=self.c1, tariff=self.tariff, status="active",
                               start_date="2026-08-01", radius_connection_type="pppoe",
                               ip_assignment_mode="manual", static_ip="197.80.1.10",
                               radius_username="anton_second")
        rows = self._list(search="anton")["results"]
        self.assertEqual(len(rows), 1)

    def test_terminated_service_ip_is_not_shown(self):
        self.s2.status = "terminated"
        self.s2.save()
        rows = {r["full_name"]: r["public_ips"] for r in self._list()["results"]}
        self.assertEqual(rows["Janine Erasmus"], [])

    def test_query_count_does_not_grow_with_customers(self):
        def count():
            with CaptureQueriesContext(connection) as ctx:
                self._list()
            return len(ctx)

        before = count()
        for i in range(10):
            c = Customer.objects.create(full_name=f"Extra {i}", email=f"e{i}@x.com")
            s = Service.objects.create(customer=c, tariff=self.tariff, status="active",
                                       start_date="2026-08-01", radius_connection_type="pppoe",
                                       ip_assignment_mode="manual", static_ip=f"197.80.2.{i}")
        after = count()
        self.assertEqual(before, after, f"{before} -> {after} queries: the prefetch is not holding")
