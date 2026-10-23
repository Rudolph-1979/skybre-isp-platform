"""Maintenance invoices on a vehicle and its running cost per km (13 Oct 2026).

Pinned here: maintenance counts EXCLUDING VAT; only invoices inside the fuel
record's distance are spread per km; running cost is null whenever the fuel
figure is; and a workshop invoice links to one service, of its own vehicle.
"""
import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from expenses.models import Expense
from fleet.models import FuelLog, ServiceRecord, Vehicle

User = get_user_model()


class MaintenanceCostTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="boss", password="x", role="admin")
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        self.vehicle = Vehicle.objects.create(
            make="Ford", model="Ranger", year=2024, registration_number="JDG281FS",
            fuel_type=Vehicle.FuelType.DIESEL, service_interval_km=15000,
        )
        self.other = Vehicle.objects.create(make="Toyota", model="Hilux", year=2022, registration_number="OTHER1")
        self.today = datetime.date(2026, 10, 13)
        # Fuel: 1 000 km between fills, R600 after the first -> R0.60/km.
        self._fill(datetime.date(2026, 10, 1), "40", "500", 1000)
        self._fill(datetime.date(2026, 10, 10), "30", "600", 2000)

    def _fill(self, day, litres, cost, km):
        return FuelLog.objects.create(
            vehicle=self.vehicle, filled_at=day, litres=Decimal(litres),
            cost=None if cost is None else Decimal(cost), odometer_km=km,
        )

    def _expense(self, day, excl, vehicle=None, rate="15"):
        return Expense.objects.create(
            description="Service", date=day, amount_excl_vat=Decimal(excl), vat_rate_pct=Decimal(rate),
            supplier_name="Kroonstad Ford", vehicle=vehicle or self.vehicle,
        )

    def test_running_cost_is_fuel_plus_maintenance_excl_vat(self):
        self._expense(datetime.date(2026, 10, 5), "1000")      # inside the fuel window
        self._expense(datetime.date(2026, 9, 1), "400")        # before it
        s = self.vehicle.maintenance_summary(self.today)
        self.assertEqual(s["lifetime"], Decimal("1400.00"))
        self.assertEqual(s["count"], 2)
        self.assertEqual(s["rand_per_km"], Decimal("1.00"))
        self.assertEqual(s["running_rand_per_km"], Decimal("1.60"))
        self.assertEqual(s["outside_window_count"], 1)
        self.assertEqual(s["month"], Decimal("1000.00"))

    def test_no_maintenance_means_running_cost_is_fuel_only(self):
        s = self.vehicle.maintenance_summary(self.today)
        self.assertEqual(s["rand_per_km"], Decimal("0.00"))
        self.assertEqual(s["running_rand_per_km"], Decimal("0.60"))

    def test_running_cost_blank_while_fuel_cost_is(self):
        self._fill(datetime.date(2026, 10, 12), "20", None, 2400)
        self._expense(datetime.date(2026, 10, 5), "1000")
        self.assertIsNone(self.vehicle.maintenance_summary(self.today)["running_rand_per_km"])

    def test_vehicle_api_lists_invoices(self):
        e = self._expense(datetime.date(2026, 10, 5), "1000")
        data = self.client.get(f"/api/vehicles/{self.vehicle.pk}/").data
        self.assertEqual(data["maintenance_summary"]["lifetime"], "1000.00")
        self.assertEqual(data["maintenance_invoices"][0]["id"], e.pk)
        self.assertEqual(data["maintenance_invoices"][0]["amount_incl_vat"], "1150.00")
        self.assertIsNone(data["maintenance_invoices"][0]["service_record"])

    def test_mark_serviced_links_the_invoice_once(self):
        e = self._expense(datetime.date(2026, 10, 5), "1000")
        r = self.client.post(f"/api/vehicles/{self.vehicle.pk}/mark_serviced/",
                             {"service_date": "2026-10-05", "odometer_km": 1500, "expense": e.pk}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        record = ServiceRecord.objects.get(vehicle=self.vehicle)
        self.assertEqual(record.expense_id, e.pk)
        self.assertEqual(r.data["maintenance_invoices"][0]["service_record"], record.pk)
        again = self.client.post(f"/api/vehicles/{self.vehicle.pk}/mark_serviced/",
                                 {"service_date": "2026-10-06", "odometer_km": 1600, "expense": e.pk}, format="json")
        self.assertEqual(again.status_code, 400)
        self.assertIn("expense", again.data)
        history = self.client.get(f"/api/service-records/?vehicle={self.vehicle.pk}").data
        rows = history["results"] if isinstance(history, dict) else history
        self.assertEqual(rows[0]["expense_amount_excl_vat"], "1000.00")
        self.assertIn("Kroonstad Ford", rows[0]["expense_label"])

    def test_mark_serviced_refuses_another_vehicles_invoice(self):
        e = self._expense(datetime.date(2026, 10, 5), "1000", vehicle=self.other)
        r = self.client.post(f"/api/vehicles/{self.vehicle.pk}/mark_serviced/",
                             {"service_date": "2026-10-05", "odometer_km": 1500, "expense": e.pk}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertFalse(ServiceRecord.objects.exists())

    def test_deleting_the_expense_keeps_the_service(self):
        e = self._expense(datetime.date(2026, 10, 5), "1000")
        self.client.post(f"/api/vehicles/{self.vehicle.pk}/mark_serviced/",
                         {"service_date": "2026-10-05", "odometer_km": 1500, "expense": e.pk}, format="json")
        e.delete()
        self.assertIsNone(ServiceRecord.objects.get(vehicle=self.vehicle).expense_id)

    def test_expense_api_vehicle_field_and_choices(self):
        r = self.client.post("/api/expenses/", {
            "description": "Tyres", "date": "2026-10-07", "amount_excl_vat": "3000", "vat_rate_pct": "15",
            "supplier_name": "Tiger Wheel", "category": "other", "vehicle": self.vehicle.pk,
        }, format="multipart")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["vehicle_label"], "JDG281FS")
        cleared = self.client.patch(f"/api/expenses/{r.data['id']}/", {"vehicle": ""}, format="multipart")
        self.assertEqual(cleared.status_code, 200, cleared.data)
        self.assertIsNone(cleared.data["vehicle"])
        choices = self.client.get("/api/expenses/vehicle-choices/").data
        self.assertEqual([c["id"] for c in choices][:2], sorted([self.vehicle.pk, self.other.pk], key=lambda pk: Vehicle.objects.get(pk=pk).registration_number))
