"""The firewall must drop the address the customer is actually using.

A suspended PPPoE service is handed a walled-garden address by RADIUS, and
its normal customer-pool address is deliberately left assigned so it can be
handed back on reactivation. So the service holds two addresses.

resolve_service_ip -- which builds the router's blocked-address list --
took whichever sorted first by string and no category into account. That
put the drop rule on an address the suspended customer was not using, so
the block did not bite; and once that customer-pool address was recycled to
somebody else, the rule would have dropped a paying customer's traffic.
"""
from decimal import Decimal

from django.test import TestCase

from billing.models import Service, Tariff
from customers.models import Customer
from network.models import IPAddress, IPPool
from network.router_sync import resolve_service_ip
from radiusauth.signals import _resolve_framed_ip


class BlockListAddressTests(TestCase):
    def setUp(self):
        self.tariff = Tariff.objects.create(
            name="Home 10", price=Decimal("500.00"),
            speed_download_kbps=10240, speed_upload_kbps=10240,
        )
        self.customer = Customer.objects.create(full_name="Suspended Sam", email="ss@example.com")
        self.customer_pool = IPPool.objects.create(
            name="Customers", category=IPPool.Category.CUSTOMER, network_cidr="10.50.0.0/24"
        )
        self.walled_pool = IPPool.objects.create(
            name="Walled garden", category=IPPool.Category.WALLED_GARDEN,
            network_cidr="172.31.0.0/24",
        )
        # "10.50.0.10" sorts before "172.31.0.5" as a string, which is how
        # the old lookup picked the wrong one.
        self.normal = IPAddress.objects.create(pool=self.customer_pool, address="10.50.0.10")
        IPAddress.objects.create(pool=self.walled_pool, address="172.31.0.5")
        self.service = Service.objects.create(
            customer=self.customer, tariff=self.tariff, status=Service.Status.ACTIVE,
            start_date="2026-01-01", radius_username="pppoe_sam", radius_password="pw",
            radius_connection_type=Service.ConnectionType.PPPOE,
            ip_assignment_mode=Service.IPAssignmentMode.POOL,
        )
        IPAddress.objects.filter(pk=self.normal.pk).update(
            assigned_service=self.service, status=IPAddress.Status.ASSIGNED
        )

    def _suspend(self):
        self.service.status = Service.Status.SUSPENDED
        self.service.save(update_fields=["status"])
        self.service.refresh_from_db()

    def test_an_active_service_blocks_and_shapes_its_real_address(self):
        self.assertEqual(resolve_service_ip(self.service), "10.50.0.10")

    def test_a_suspended_service_is_blocked_on_its_walled_garden_address(self):
        self._suspend()
        framed = _resolve_framed_ip(self.service)
        self.service.refresh_from_db()
        self.assertEqual(framed, "172.31.0.5", "RADIUS hands out the walled-garden address")
        self.assertEqual(
            resolve_service_ip(self.service), framed,
            "the drop rule must target the address the customer is actually on",
        )

    def test_it_is_not_their_old_customer_address(self):
        """Explicit, because that address gets recycled: dropping it would
        eventually cut off whoever holds it next."""
        self._suspend()
        _resolve_framed_ip(self.service)
        self.service.refresh_from_db()
        self.assertNotEqual(resolve_service_ip(self.service), "10.50.0.10")

    def test_with_no_walled_garden_pool_it_falls_back_to_the_known_address(self):
        """A deployment that has not configured a walled garden never gets
        such an address, and there the last known one is the best guess."""
        IPAddress.objects.filter(pool=self.walled_pool).delete()
        self._suspend()
        self.assertEqual(resolve_service_ip(self.service), "10.50.0.10")
