"""Where the client connects, and who is behind a given box.

The failure this guards against is conflation. Service.device is the NAS
-- the router the line terminates on and the one every RADIUS, shaper and
blocking action is pushed to. Service.access_device is the AP, sector, OLT
or switch the client physically connects to. On a wireless network one
core router carries every customer, so if these two ever collapse into
each other, "who is on this AP" silently starts returning the whole book
and enforcement starts being aimed at a radio that has no RADIUS
relationship with the service.
"""
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from billing.models import Service, Tariff
from customers.models import Customer, Partner
from network.models import Device, NetworkSite


class AccessDeviceTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(username="ops", password="x", role=User.Role.ADMIN)
        self.site = NetworkSite.objects.create(title="Tower 3")
        self.nas = Device.objects.create(
            name="Core Router", device_type=Device.DeviceType.ROUTER, ip_address="10.0.0.1"
        )
        self.sector = Device.objects.create(
            name="Tower3 Sector B", device_type=Device.DeviceType.AP, ip_address="10.0.0.50", site=self.site
        )
        self.tariff = Tariff.objects.create(
            name="20 Mbps", price=Decimal("899.00"),
            speed_download_kbps=20480, speed_upload_kbps=20480,
        )
        self.customer = Customer.objects.create(
            full_name="Nomsa Dlamini", email="n@example.com", phone="0821234567"
        )

    def _service(self, **kwargs):
        return Service.objects.create(
            customer=kwargs.pop("customer", self.customer),
            tariff=self.tariff,
            status=Service.Status.ACTIVE,
            start_date="2026-08-01",
            **kwargs,
        )

    def test_the_access_device_is_independent_of_the_nas(self):
        """Both set, both kept, neither overwriting the other."""
        service = self._service(device=self.nas, access_device=self.sector, access_detail="Sector B 120°")
        service.refresh_from_db()
        self.assertEqual(service.device, self.nas)
        self.assertEqual(service.access_device, self.sector)
        self.assertEqual(service.access_detail, "Sector B 120°")

    def test_it_is_optional_and_can_be_cleared(self):
        """Most existing lines will never have one filled in, and staff
        must be able to undo a wrong pick without deleting the service."""
        service = self._service(access_device=self.sector)
        self.client.force_authenticate(self.admin)
        res = self.client.patch(f"/api/services/{service.pk}/", {"access_device": None}, format="json")
        self.assertEqual(res.status_code, 200)
        service.refresh_from_db()
        self.assertIsNone(service.access_device)

    def test_deleting_the_device_does_not_delete_the_service(self):
        """SET_NULL, not CASCADE. Decommissioning a radio must not delete
        the customer lines that used to point at it."""
        service = self._service(access_device=self.sector)
        self.sector.delete()
        service.refresh_from_db()
        self.assertIsNone(service.access_device)
        self.assertEqual(service.status, Service.Status.ACTIVE)

    def test_the_service_api_reports_the_device_and_its_site(self):
        """"Tower 3" is what somebody says on the phone, not the name of
        the radio bolted to it."""
        service = self._service(access_device=self.sector, access_detail="PON 1/3")
        self.client.force_authenticate(self.admin)
        res = self.client.get(f"/api/services/{service.pk}/")
        self.assertEqual(res.data["access_device_name"], "Tower3 Sector B")
        self.assertEqual(res.data["access_site_name"], "Tower 3")
        self.assertEqual(res.data["access_device_type"], "Access Point")
        self.assertEqual(res.data["access_detail"], "PON 1/3")


class ConnectedCustomersTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(username="ops", password="x", role=User.Role.ADMIN)
        self.nas = Device.objects.create(
            name="Core Router", device_type=Device.DeviceType.ROUTER, ip_address="10.0.0.1"
        )
        self.sector = Device.objects.create(
            name="Sector B", device_type=Device.DeviceType.AP, ip_address="10.0.0.50"
        )
        self.tariff = Tariff.objects.create(
            name="T", price=Decimal("1"), speed_download_kbps=1024, speed_upload_kbps=1024
        )

    def _service(self, name, **kwargs):
        customer = Customer.objects.create(
            full_name=name, email=f"{name.replace(' ', '')}@x.com", phone="0820000000",
            partner=kwargs.pop("partner", None),
        )
        return Service.objects.create(
            customer=customer, tariff=self.tariff, status=kwargs.pop("status", Service.Status.ACTIVE),
            start_date="2026-08-01", **kwargs,
        )

    def test_it_lists_who_connects_through_the_device(self):
        self._service("On The Sector", access_device=self.sector, access_detail="B1")
        self.client.force_authenticate(self.admin)
        res = self.client.get(f"/api/devices/{self.sector.pk}/connected-customers/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["count"], 1)
        row = res.data["results"][0]
        self.assertEqual(row["customer_name"], "On The Sector")
        # The phone number is on the row because the next action during an
        # outage is always a call.
        self.assertEqual(row["customer_phone"], "0820000000")
        self.assertEqual(row["access_detail"], "B1")

    def test_it_does_NOT_list_by_the_nas(self):
        """The trap this whole design exists to avoid: one core router
        carries every customer, so listing by it returns the entire book
        and the answer becomes useless on the day it is needed."""
        self._service("Terminates On Core", device=self.nas)
        self.client.force_authenticate(self.admin)
        res = self.client.get(f"/api/devices/{self.nas.pk}/connected-customers/")
        self.assertEqual(res.data["count"], 0)

    def test_terminated_lines_are_left_out(self):
        """Phoning somebody whose service ended months ago to tell them
        their tower is down is worse than not calling."""
        self._service("Gone", access_device=self.sector, status=Service.Status.TERMINATED)
        self._service("Still Here", access_device=self.sector)
        self.client.force_authenticate(self.admin)
        res = self.client.get(f"/api/devices/{self.sector.pk}/connected-customers/")
        self.assertEqual([r["customer_name"] for r in res.data["results"]], ["Still Here"])

    def test_partner_scoping_applies(self):
        """A staff member restricted to one reseller must not learn
        another reseller's customer names through the networking page."""
        mine = Partner.objects.create(name="Mine")
        theirs = Partner.objects.create(name="Theirs")
        self._service("Mine", access_device=self.sector, partner=mine)
        self._service("Theirs", access_device=self.sector, partner=theirs)
        self._service("Direct", access_device=self.sector)

        tech = User.objects.create_user(
            username="tech", password="x", role=User.Role.TECHNICIAN,
            allowed_sections=["networking"], allowed_partners=[mine.pk],
        )
        self.client.force_authenticate(tech)
        res = self.client.get(f"/api/devices/{self.sector.pk}/connected-customers/")
        self.assertEqual({r["customer_name"] for r in res.data["results"]}, {"Mine", "Direct"})

    def test_the_device_list_carries_the_count(self):
        """So the cost of an outage is visible before anyone opens
        anything."""
        self._service("A", access_device=self.sector)
        self._service("B", access_device=self.sector)
        self._service("Ended", access_device=self.sector, status=Service.Status.TERMINATED)
        self.client.force_authenticate(self.admin)
        res = self.client.get("/api/devices/")
        counts = {d["name"]: d["access_service_count"] for d in res.data["results"]}
        self.assertEqual(counts["Sector B"], 2)
        self.assertEqual(counts["Core Router"], 0)

    def test_it_needs_networking_access(self):
        self._service("Somebody", access_device=self.sector)
        desk = User.objects.create_user(
            username="desk", password="x", role=User.Role.SUPPORT, allowed_sections=["customers"]
        )
        self.client.force_authenticate(desk)
        res = self.client.get(f"/api/devices/{self.sector.pk}/connected-customers/")
        self.assertEqual(res.status_code, 403)
