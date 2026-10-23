"""Stock sites -- the places stock can be.

The rules worth a test are the two that are silent when they break:

  * exactly one site is the default. Two defaults means "where is this
    stock" has two answers and nobody notices until a count is wrong.
  * the default site cannot be deleted or retired out from under the
    stock that resolves to it.
"""
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from fleet.models import Vehicle
from inventory.models import StockSite


class StockSiteDefaultTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.staff = User.objects.create_user(username="stores", password="x", role=User.Role.ADMIN)
        self.client.force_authenticate(self.staff)

    def test_promoting_a_site_demotes_the_previous_default(self):
        first = StockSite.objects.create(name="Head Office Store", is_default=True)
        second = StockSite.objects.create(name="Bethlehem Store", is_default=True)

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertFalse(first.is_default)
        self.assertTrue(second.is_default)
        self.assertEqual(StockSite.objects.filter(is_default=True).count(), 1)

    def test_promoting_over_the_api_demotes_the_previous_default(self):
        first = StockSite.objects.create(name="Head Office Store", is_default=True)
        second = StockSite.objects.create(name="Spare Container")

        res = self.client.patch(f"/api/stock-sites/{second.pk}/", {"is_default": True}, format="json")

        self.assertEqual(res.status_code, 200, res.content)
        first.refresh_from_db()
        self.assertFalse(first.is_default)
        self.assertEqual(StockSite.objects.filter(is_default=True).count(), 1)

    def test_saving_the_default_again_keeps_it_default(self):
        """A plain edit of the default site (renaming it, say) must not
        demote it -- save() excludes the row being saved from the demotion."""
        site = StockSite.objects.create(name="Head Office Store", is_default=True)
        site.name = "Head Office Stores"
        site.save()

        site.refresh_from_db()
        self.assertTrue(site.is_default)

    def test_default_site_cannot_be_deleted(self):
        site = StockSite.objects.create(name="Head Office Store", is_default=True)

        res = self.client.delete(f"/api/stock-sites/{site.pk}/")

        self.assertEqual(res.status_code, 400, res.content)
        self.assertTrue(StockSite.objects.filter(pk=site.pk).exists())

    def test_a_non_default_site_can_be_deleted(self):
        StockSite.objects.create(name="Head Office Store", is_default=True)
        spare = StockSite.objects.create(name="Old Container")

        res = self.client.delete(f"/api/stock-sites/{spare.pk}/")

        self.assertEqual(res.status_code, 204, res.content)
        self.assertFalse(StockSite.objects.filter(pk=spare.pk).exists())

    def test_default_site_cannot_be_retired(self):
        site = StockSite.objects.create(name="Head Office Store", is_default=True)

        res = self.client.patch(f"/api/stock-sites/{site.pk}/", {"is_active": False}, format="json")

        self.assertEqual(res.status_code, 400, res.content)
        site.refresh_from_db()
        self.assertTrue(site.is_active)

    def test_default_site_helper_ignores_retired_sites(self):
        self.assertIsNone(StockSite.default_site())
        site = StockSite.objects.create(name="Head Office Store", is_default=True)
        self.assertEqual(StockSite.default_site(), site)


class StockSiteVehicleTests(TestCase):
    """A vehicle site points at the real bakkie in Fleet. Registration and
    driver stay there -- one source of truth, so re-registering a vehicle
    or handing it to another technician can't leave stock records stale."""

    def setUp(self):
        self.client = APIClient()
        self.staff = User.objects.create_user(username="stores", password="x", role=User.Role.ADMIN)
        self.client.force_authenticate(self.staff)
        self.driver = User.objects.create_user(
            username="pieter", password="x", role=User.Role.TECHNICIAN,
            first_name="Pieter", last_name="B",
        )
        self.vehicle = Vehicle.objects.create(
            make="Toyota", model="Hilux", year=2021,
            registration_number="ABC123GP", assigned_to=self.driver,
        )

    def test_vehicle_site_requires_a_vehicle(self):
        res = self.client.post(
            "/api/stock-sites/", {"name": "Bakkie 1", "site_type": "vehicle"}, format="json"
        )
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn("vehicle", res.json())

    def test_store_room_cannot_be_linked_to_a_vehicle(self):
        res = self.client.post(
            "/api/stock-sites/",
            {"name": "Store", "site_type": "warehouse", "vehicle": self.vehicle.pk},
            format="json",
        )
        self.assertEqual(res.status_code, 400, res.content)

    def test_holder_of_a_vehicle_site_is_the_fleet_driver(self):
        res = self.client.post(
            "/api/stock-sites/",
            {"name": "Bakkie 1", "site_type": "vehicle", "vehicle": self.vehicle.pk},
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.content)
        body = res.json()
        self.assertEqual(body["holder_name"], "Pieter B")
        self.assertEqual(body["vehicle_registration"], "ABC123GP")

    def test_one_stock_site_per_vehicle(self):
        StockSite.objects.create(
            name="Bakkie 1", site_type=StockSite.SiteType.VEHICLE, vehicle=self.vehicle
        )
        res = self.client.post(
            "/api/stock-sites/",
            {"name": "Bakkie 1 again", "site_type": "vehicle", "vehicle": self.vehicle.pk},
            format="json",
        )
        self.assertEqual(res.status_code, 400, res.content)


class StockSiteAccessTests(TestCase):
    """Reading the site list is open to any staff member; changing it is not.

    Same split as suppliers, and for the same reason: Scheduling needs to
    say which bakkie a technician's stock is in without being handed the
    Inventory section.
    """

    def setUp(self):
        self.client = APIClient()

    def test_staff_without_inventory_can_list_but_not_create(self):
        user = User.objects.create_user(username="scheduler", password="x", role=User.Role.SUPPORT)
        user.allowed_sections = ["scheduling"]
        user.save()
        self.client.force_authenticate(user)
        StockSite.objects.create(name="Head Office Store", is_default=True)

        self.assertEqual(self.client.get("/api/stock-sites/").status_code, 200)
        res = self.client.post("/api/stock-sites/", {"name": "Sneaky Store"}, format="json")
        self.assertEqual(res.status_code, 403, res.content)

    def test_customer_cannot_see_sites_at_all(self):
        customer_user = User.objects.create_user(
            username="client", password="x", role=User.Role.CUSTOMER
        )
        self.client.force_authenticate(customer_user)

        self.assertEqual(self.client.get("/api/stock-sites/").status_code, 403)
