"""#201: FNB Fleetcard statement import into the fuel log."""
import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from fleet import fleetcard
from fleet.models import FuelLog, OdometerReading, Vehicle

User = get_user_model()

HEADER = ("01e01eCSD781e84389eCOS0001eSKYBRE PTY LTD                          eSKYBRE PTY LTD                          "
          "e112 STREET                              eKURUMAN                                  e                                        "
          "e9500eDUBULA              eS   eGRUNDER             eR   eMR  e02e20260930e20260930e202804")


def fill(reg, date, odo, hhmmss, garage, merchant, amount, litres):
    rest = "eCe2e05e00" + "e00" * 17 + "e01e000044Ce071e00000{eSVT"
    return (f"01e03eCSD781e84389eCOS0001e{reg:<10}e01e4e{date}e{odo:07d}e000000eE0{hhmmss}"
            f"e{garage:<40}{merchant:>8}e96e01e{amount}e{litres}{rest}")


SAMPLE = "\r\n".join([
    HEADER,
    "01e02eCSD781e84389eCOS0001eHKS313FSe01e5eAe20250529",           # card details: ignored
    fill("HKS313FS", "20260901", 463457, "161938", "WILLIAMS HUNT DELTA FSTATE-NORTHERN", "11128164", "0000019200{", "00000680A"),
    fill("HKS313FS", "20260904", 463952, "151845", "WILLIAMS HUNT DELTA FSTATE-NORTHERN", "11128164", "0000013000{", "00000414G"),
    fill("DFR446NC", "20260911", 294296, "163210", "HECTOR AUSTIN HENNEMAN FSTATE-NORTHERN", "18732452", "0000010000{", "00000317D"),
    fill("HKS313FS", "20260906", 464000, "101010", "SOME GARAGE", "00000001", "0000010000}", "00000317D"),  # reversal
    "09e01e84389eCOS0001eHKS313FS  e01e5e0007237",                   # monthly stats: ignored
    "",
])


class ParseTests(TestCase):
    def test_overpunch(self):
        self.assertEqual(fleetcard.overpunch("0000010000{"), Decimal("1000.00"))
        self.assertEqual(fleetcard.overpunch("00000317D"), Decimal("31.74"))
        self.assertEqual(fleetcard.overpunch("0000014767C"), Decimal("1476.73"))
        self.assertEqual(fleetcard.overpunch("0000010000}"), Decimal("-1000.00"))
        self.assertEqual(fleetcard.overpunch("0000001500J"), Decimal("-150.01"))

    def test_fill_lines(self):
        parsed = fleetcard.parse(SAMPLE)
        self.assertEqual(parsed["client"], "84389")
        self.assertEqual(parsed["statement_date"], datetime.date(2026, 9, 30))
        first = parsed["fills"][0]
        self.assertEqual((first["registration"], first["date"], first["time"], first["odometer_km"]),
                         ("HKS313FS", datetime.date(2026, 9, 1), datetime.time(16, 19, 38), 463457))
        self.assertEqual((first["amount"], first["litres"], first["garage"], first["merchant_no"]),
                         (Decimal("1920.00"), Decimal("68.01"), "WILLIAMS HUNT DELTA FSTATE-NORTHERN", "11128164"))
        self.assertEqual(len(parsed["fills"]), 4)
        self.assertEqual(parsed["errors"], [])

    def test_not_a_fleetcard_file(self):
        with self.assertRaises(fleetcard.FleetcardError):
            fleetcard.parse("Client Code,Client Name\n1,2\n")


class ImportTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="boss", password="x", role="admin", first_name="Rudolph")
        self.api = APIClient()
        self.api.force_authenticate(self.user)
        self.hilux = Vehicle.objects.create(make="Toyota", model="Hilux", year=2015, registration_number="HKS 313 FS")

    def post(self, commit=False, text=SAMPLE):
        data = {"file": SimpleUploadedFile("84389.txt", text.encode("latin-1"))}
        if commit:
            data["commit"] = "1"
        res = self.api.post("/api/fuel-logs/import-fleetcard/", data, format="multipart")
        self.assertEqual(res.status_code, 200, getattr(res, "data", None))
        return res.data

    def test_preview_changes_nothing(self):
        data = self.post()
        self.assertFalse(data["committed"])
        self.assertEqual(data["counts"], {"new": 2, "no_vehicle": 1, "reversal": 1})
        self.assertEqual(FuelLog.objects.count(), 0)

    def test_import_then_again_adds_nothing(self):
        data = self.post(commit=True)
        self.assertEqual((data["created"], data["odometer_readings"]), (2, 2))
        log = FuelLog.objects.get(filled_at=datetime.date(2026, 9, 1))
        self.assertEqual((log.vehicle, log.litres, log.cost, log.odometer_km, log.source, log.station),
                         (self.hilux, Decimal("68.01"), Decimal("1920.00"), 463457, "fleetcard",
                          "WILLIAMS HUNT DELTA FSTATE-NORTHERN"))
        self.assertEqual(log.logged_by, self.user)
        self.assertEqual(log.price_per_litre, Decimal("28.23"))
        reading = OdometerReading.objects.get(vehicle=self.hilux, reading_km=463457)
        self.assertEqual((reading.source, reading.recorded_by), ("fleetcard", None))
        res = self.api.get(f"/api/odometer-readings/?vehicle={self.hilux.id}")
        rows = res.data["results"] if isinstance(res.data, dict) else res.data
        self.assertEqual(rows[-1]["recorded_by_name"], "FNB Fleetcard")
        again = self.post(commit=True)
        self.assertEqual(again["created"], 0)
        self.assertEqual(again["counts"].get("imported"), 2)
        self.assertEqual(FuelLog.objects.count(), 2)

    def test_fill_already_captured_by_hand_is_not_doubled(self):
        FuelLog.objects.create(vehicle=self.hilux, filled_at=datetime.date(2026, 9, 4), litres=Decimal("41.20"),
                               cost=Decimal("1300"), odometer_km=463950)
        data = self.post(commit=True)
        self.assertEqual(data["created"], 1)
        row = next(r for r in data["rows"] if r["date"] == "2026-09-04")
        self.assertEqual(row["status"], "captured")
        self.assertEqual(FuelLog.objects.filter(vehicle=self.hilux).count(), 2)

    def test_needs_vehicles_access(self):
        clerk = User.objects.create_user(username="clerk", password="x", role="support")
        self.api.force_authenticate(clerk)
        res = self.api.post("/api/fuel-logs/import-fleetcard/",
                            {"file": SimpleUploadedFile("84389.txt", SAMPLE.encode())}, format="multipart")
        self.assertIn(res.status_code, (403,))
