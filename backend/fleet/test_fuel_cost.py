"""What a vehicle costs in fuel, and what the figure is allowed to hide.

Two things are pinned here, and both are about a number being wrong in a
way nobody would notice.

A NULL IS NOT A ZERO. Every fill-up logged before the cost field existed
has none. If those counted as R0.00 the monthly spend would read low,
look perfectly plausible, and be quietly wrong for as long as anybody
used it. They are excluded, and the count of them travels with the
total so the page can say so.

A COST PER KILOMETRE BUILT FROM HALF THE RECORD IS WORSE THAN NO FIGURE.
The distance comes from every fill-up; the money comes only from the
costed ones. Mixing them understates the answer by exactly the missing
proportion and produces a number that looks entirely reasonable. So it
is null until the record is complete.
"""
import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from fleet.models import FuelLog, Vehicle

User = get_user_model()


class FuelCostTests(TestCase):
    def setUp(self):
        self.vehicle = Vehicle.objects.create(
            make="Ford", model="Ranger", year=2024, registration_number="JDG281FS",
            fuel_type=Vehicle.FuelType.DIESEL, service_interval_km=15000,
        )
        self.today = datetime.date(2026, 9, 17)

    def _fill(self, day, litres, cost, km):
        return FuelLog.objects.create(
            vehicle=self.vehicle, filled_at=day, litres=Decimal(litres),
            cost=None if cost is None else Decimal(cost), odometer_km=km,
        )

    # --- the derived figure ----------------------------------------------

    def test_rand_per_litre_is_derived_not_stored(self):
        log = self._fill(self.today, "47.84", "1052.48", 89936)

        self.assertEqual(log.price_per_litre, Decimal("22.00"))

    def test_a_fill_up_with_no_cost_has_no_rand_per_litre(self):
        log = self._fill(self.today, "47.84", None, 89936)

        self.assertIsNone(log.price_per_litre)

    # --- the totals -------------------------------------------------------

    def test_the_month_year_and_lifetime_totals(self):
        self._fill(datetime.date(2026, 9, 2), "50", "1100.00", 89000)
        self._fill(datetime.date(2026, 9, 17), "40", "880.00", 89500)
        self._fill(datetime.date(2026, 3, 4), "45", "990.00", 85000)
        self._fill(datetime.date(2025, 11, 1), "45", "900.00", 70000)

        summary = self.vehicle.fuel_summary(today=self.today)

        self.assertEqual(summary["month"], Decimal("1980.00"))
        self.assertEqual(summary["year"], Decimal("2970.00"))
        self.assertEqual(summary["lifetime"], Decimal("3870.00"))

    def test_fill_ups_with_no_cost_are_excluded_and_counted(self):
        """The whole point. A total that silently omits them is the figure
        somebody takes into a budget conversation."""
        self._fill(datetime.date(2026, 9, 2), "50", "1100.00", 89000)
        self._fill(datetime.date(2026, 9, 10), "50", None, 89400)
        self._fill(datetime.date(2026, 3, 4), "45", None, 85000)

        summary = self.vehicle.fuel_summary(today=self.today)

        self.assertEqual(summary["month"], Decimal("1100.00"))
        self.assertEqual(summary["missing_cost_count"], 2)
        self.assertEqual(summary["month_missing_cost_count"], 1)

    def test_a_vehicle_with_no_costed_fill_ups_reports_zero_not_none(self):
        """Zero spend is a real answer; the page distinguishes it from "no
        data" using the missing count beside it."""
        self._fill(self.today, "50", None, 89000)

        summary = self.vehicle.fuel_summary(today=self.today)

        self.assertEqual(summary["lifetime"], Decimal("0"))
        self.assertIsNone(summary["average_rand_per_litre"])
        self.assertEqual(summary["missing_cost_count"], 1)

    def test_the_average_rand_per_litre_uses_only_costed_litres(self):
        """Dividing costed money by ALL litres would understate the pump
        price by however much fuel has no cost against it."""
        self._fill(datetime.date(2026, 9, 2), "50", "1100.00", 89000)
        self._fill(datetime.date(2026, 9, 10), "50", None, 89400)

        summary = self.vehicle.fuel_summary(today=self.today)

        self.assertEqual(summary["average_rand_per_litre"], Decimal("22.00"))

    # --- cost per km ------------------------------------------------------

    def test_cost_per_km_is_withheld_while_any_fill_up_lacks_a_cost(self):
        self._fill(datetime.date(2026, 9, 1), "50", "1100.00", 89000)
        self._fill(datetime.date(2026, 9, 10), "50", None, 89500)
        self._fill(datetime.date(2026, 9, 15), "50", "1100.00", 90000)

        self.assertIsNone(self.vehicle.fuel_summary(today=self.today)["rand_per_km"])

    def test_cost_per_km_once_the_record_is_complete(self):
        """Full-to-full, same as the consumption figure: the first fill-up
        paid for distance covered before this record starts, so its cost is
        not part of the distance being measured."""
        self._fill(datetime.date(2026, 9, 1), "50", "1100.00", 89000)
        self._fill(datetime.date(2026, 9, 15), "50", "1100.00", 90000)

        summary = self.vehicle.fuel_summary(today=self.today)

        # R1,100 over the 1,000 km that second tank covered.
        self.assertEqual(summary["rand_per_km"], Decimal("1.10"))

    def test_one_costed_fill_up_is_not_enough_for_a_cost_per_km(self):
        self._fill(self.today, "50", "1100.00", 89000)

        self.assertIsNone(self.vehicle.fuel_summary(today=self.today)["rand_per_km"])


class FuelCostApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(
            User.objects.create_user(username="boss", password="x", role="admin", is_staff=True)
        )
        self.vehicle = Vehicle.objects.create(
            make="Ford", model="Ranger", year=2024, registration_number="JDG281FS",
            service_interval_km=15000,
        )

    def _post(self, **extra):
        payload = {
            "vehicle": self.vehicle.pk, "filled_at": "2026-09-17",
            "litres": "47.84", "odometer_km": 89936, **extra,
        }
        return self.client.post("/api/fuel-logs/", payload, format="json")

    def test_a_fill_up_can_be_logged_with_a_cost(self):
        res = self._post(cost="1052.48")

        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()["price_per_litre"], "22.00")

    def test_a_fill_up_can_still_be_logged_without_one(self):
        """Somebody at a pump without the slip must not be blocked from
        recording the litres and the odometer."""
        res = self._post()

        self.assertEqual(res.status_code, 201, res.content)
        self.assertIsNone(res.json()["cost"])

    def test_litres_typed_into_the_cost_box_is_refused(self):
        """R47.84 for 47.84 L is R1 a litre. It lands in the monthly spend
        looking like a real number and is then very hard to find."""
        res = self._post(cost="47.84")

        self.assertEqual(res.status_code, 400)
        self.assertIn("a litre", str(res.json()["cost"]))

    def test_a_slipped_decimal_is_refused(self):
        res = self._post(cost="10524.80")

        self.assertEqual(res.status_code, 400)

    def test_a_normal_pump_price_is_accepted_at_both_ends_of_the_range(self):
        self.assertEqual(self._post(cost="900.00").status_code, 201)   # ~R18.81/L
        self.assertEqual(
            self._post(cost="1300.00", odometer_km=90500).status_code, 201  # ~R27.17/L
        )

    def test_a_cost_can_be_added_to_a_fill_up_logged_without_one(self):
        """The ordinary case: logged at the pump, costed later off the slip.
        Before this the only way to add the rand value was to delete the
        entry and retype it, which is how an odometer reading gets lost."""
        log_id = self._post().json()["id"]

        res = self.client.patch(f"/api/fuel-logs/{log_id}/", {"cost": "1052.48"}, format="json")

        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()["price_per_litre"], "22.00")
        self.assertEqual(
            self.vehicle.fuel_summary()["lifetime"], Decimal("1052.48")
        )

    def test_a_wrong_cost_can_be_cleared_again(self):
        """Sending null has to mean "remove it", not "leave it as it was" --
        otherwise a cost entered against the wrong vehicle can never be
        taken out of that vehicle's totals."""
        log_id = self._post(cost="1052.48").json()["id"]

        res = self.client.patch(f"/api/fuel-logs/{log_id}/", {"cost": None}, format="json")

        self.assertEqual(res.status_code, 200, res.content)
        self.assertIsNone(res.json()["cost"])
        self.assertEqual(self.vehicle.fuel_summary()["missing_cost_count"], 1)

    def test_an_edit_is_checked_the_same_way_as_a_new_entry(self):
        log_id = self._post(cost="1052.48").json()["id"]

        res = self.client.patch(f"/api/fuel-logs/{log_id}/", {"cost": "47.84"}, format="json")

        self.assertEqual(res.status_code, 400)

    def test_the_vehicle_reports_its_fuel_spend(self):
        self._post(cost="1052.48")

        row = self.client.get(f"/api/vehicles/{self.vehicle.pk}/").json()

        self.assertEqual(row["fuel_summary"]["lifetime"], "1052.48")
        self.assertEqual(row["fuel_summary"]["missing_cost_count"], 0)
