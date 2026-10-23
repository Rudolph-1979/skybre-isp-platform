"""Conecto, 7 Oct 2026 (third batch): vehicle trips and fuel slips, quotes on
quoteCreated with their PDF link, and the sales dashboard summary. Nothing
here touches the network."""
import datetime
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from billing.models import Invoice, InvoiceItem, Tariff
from customers.models import Customer
from fleet.models import FuelLog, OdometerReading, TripLog, Vehicle
from sales.models import Lead

from .models import ConectoQuote

User = get_user_model()
TOKEN = "test-shared-secret"
URLS = {
    "quoteCreated": "https://example.invalid/quoteCreated",
    "invoiceCreated": "https://example.invalid/invoiceCreated",
}
CONFIGURED = override_settings(
    CONECTO_API_KEY=TOKEN, CONECTO_WEBHOOK_TOKEN=TOKEN, CONECTO_URLS=URLS,
    CONECTO_PUBLIC_BASE_URL="https://portal.skybre.co.za",
)


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = ""
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._payload


def post(path, body):
    return APIClient().post(path, body, format="json", HTTP_AUTHORIZATION=f"Bearer {TOKEN}")


@CONFIGURED
class VehicleLogTests(TestCase):
    def setUp(self):
        self.tech = User.objects.create_user(username="christo", password="x", role="technician", first_name="Christo")
        self.tech.refresh_from_db()
        self.bakkie = Vehicle.objects.create(make="Toyota", model="Hilux", year=2019,
                                             registration_number="FJ 12 AB GP", assigned_to=self.tech)
        Vehicle.objects.create(make="Nissan", model="NP200", year=2018, registration_number="CA 123-456")
        self.t0 = timezone.now().replace(microsecond=0) - datetime.timedelta(hours=3)
        OdometerReading.objects.create(vehicle=self.bakkie, reading_km=100000,
                                       recorded_at=self.t0 - datetime.timedelta(days=1))

    def trip(self, **over):
        body = {
            "conecto_trip_id": "trip-1", "vexar_vehicle_id": None, "vehicle_registration": "fj12abgp",
            "staff_code": self.tech.staff_code, "technician_code": None, "technician_name": "Christo",
            "start_odometer": 100050, "end_odometer": 100112, "km_driven": 62,
            "started_at": self.t0.isoformat(), "ended_at": (self.t0 + datetime.timedelta(hours=2)).isoformat(),
            "start_photo_url": "https://example.com/a.jpg", "end_photo_url": None, "notes": "Kroonstad installs",
        }
        body.update(over)
        return post("/api/webhooks/conecto/vehicle-km-log/", body)

    def test_vehicle_list_puts_theirs_first(self):
        res = post("/api/conecto/vehicles/", {"staff_code": self.tech.staff_code})
        self.assertEqual(res.status_code, 200, res.data)
        first = res.data["vehicles"][0]
        self.assertEqual((first["registration"], first["assigned_to_you"], first["current_odometer"]),
                         ("FJ 12 AB GP", True, 100000))
        self.assertEqual(first["vehicle_id"], str(self.bakkie.pk))

    def test_trip_is_logged_once_and_moves_the_odometer(self):
        res = self.trip()
        self.assertEqual(res.status_code, 201, res.data)
        trip = TripLog.objects.get(pk=int(res.data["log_id"]))
        self.assertEqual((trip.vehicle, trip.driver, trip.km_driven), (self.bakkie, self.tech, 62))
        self.assertTrue(trip.odometer_recorded)
        self.bakkie.refresh_from_db()
        self.assertEqual(self.bakkie.current_odometer_km, 100112)
        again = self.trip()
        self.assertEqual((again.status_code, again.data["log_id"]), (200, res.data["log_id"]))
        self.assertEqual(TripLog.objects.count(), 1)

    def test_a_reading_that_contradicts_history_is_kept_but_flagged(self):
        res = self.trip(start_odometer=90000, end_odometer=90010, km_driven=10)
        self.assertEqual(res.status_code, 201, res.data)
        self.assertFalse(TripLog.objects.get().odometer_recorded)
        self.assertEqual(self.bakkie.current_odometer_km, 100000)

    def test_unknown_vehicle_and_bad_body(self):
        self.assertEqual(self.trip(vehicle_registration="XX 99").status_code, 404)
        self.assertEqual(self.trip(conecto_trip_id="t2", end_odometer=100000).status_code, 400)
        self.assertEqual(post("/api/webhooks/conecto/vehicle-km-log/", {}).status_code, 400)

    def test_fuel_slip(self):
        body = {
            "conecto_fuel_id": "fuel-1", "vexar_vehicle_id": str(self.bakkie.pk), "vehicle_registration": "x",
            "staff_code": self.tech.staff_code, "technician_name": "Christo", "filled_at": str(timezone.localdate()),
            "odometer": 100200, "litres": 45.5, "price_per_litre": 22.1, "total_amount": 1005.55, "currency": "ZAR",
            "station": "Engen Kroonstad", "fuel_type": "diesel", "slip_url": "https://example.com/slip.jpg",
            "ai_extracted": {"litres": 45.5}, "edited_by_technician": False, "amount_mismatch": True,
            "recorded_at": timezone.now().isoformat(),
        }
        res = post("/api/webhooks/conecto/vehicle-fuel-log/", body)
        self.assertEqual(res.status_code, 201, res.data)
        log = FuelLog.objects.get(pk=int(res.data["log_id"]))
        self.assertEqual((log.cost, log.litres, log.source, log.amount_mismatch, log.station),
                         (Decimal("1005.55"), Decimal("45.50"), "conecto", True, "Engen Kroonstad"))
        self.assertEqual(log.conecto_data["ai_extracted"], {"litres": 45.5})
        again = post("/api/webhooks/conecto/vehicle-fuel-log/", body)
        self.assertEqual((again.status_code, again.data["log_id"]), (200, res.data["log_id"]))

    def test_needs_the_token(self):
        self.assertEqual(APIClient().post("/api/conecto/vehicles/", {}, format="json").status_code, 401)


@CONFIGURED
class QuoteTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(full_name="Jan Botha", customer_id="CUS-009900")

    def _quote(self, request, due=None):
        request.return_value = FakeResponse(payload={"success": True})
        with self.captureOnCommitCallbacks(execute=True):
            quote = Invoice.objects.create(customer=self.customer, status=Invoice.Status.QUOTE,
                                           date_due=due or timezone.localdate() + datetime.timedelta(days=14))
            InvoiceItem.objects.create(invoice=quote, description="Installation", quantity=1,
                                       unit_price=Decimal("1500"), tax_rate_pct=Decimal("15"))
            Invoice.objects.filter(pk=quote.pk).update(total=Decimal("1725.00"))
            quote.refresh_from_db()
            quote.save()
        return quote

    @staticmethod
    def bodies(request, endpoint):
        return [c.kwargs["json"] for c in request.call_args_list if c.args[1].endswith(endpoint)]

    @patch("conecto.client.requests.request")
    def test_new_quote_then_accepted_then_invoice(self, request):
        quote = self._quote(request)
        sent = self.bodies(request, "quoteCreated")
        self.assertEqual(sent[-1]["status"], "pending")
        self.assertEqual(sent[-1]["description"], "Installation")
        self.assertTrue(sent[-1]["pdf_available"])
        quote_number = quote.number
        request.reset_mock()
        request.return_value = FakeResponse(payload={})
        with self.captureOnCommitCallbacks(execute=True):
            quote.convert_to_invoice()
        endpoints = [c.args[1].rsplit("/", 1)[-1] for c in request.call_args_list]
        self.assertEqual(endpoints, ["quoteCreated", "invoiceCreated"])
        accepted = self.bodies(request, "quoteCreated")[0]
        quote.refresh_from_db()
        self.assertEqual((accepted["status"], accepted["quote_number"], accepted["invoice_number"]),
                         ("accepted", quote_number, quote.number))

    @patch("conecto.client.requests.request")
    def test_cancelled_quote_is_declined(self, request):
        quote = self._quote(request)
        with self.captureOnCommitCallbacks(execute=True):
            quote.status = Invoice.Status.CANCELLED
            quote.save()
        self.assertEqual(self.bodies(request, "quoteCreated")[-1]["status"], "declined")

    @patch("conecto.client.requests.request")
    def test_nightly_run_sends_expired(self, request):
        quote = self._quote(request)
        Invoice.objects.filter(pk=quote.pk).update(date_due=timezone.localdate() - datetime.timedelta(days=1))
        out = StringIO()
        call_command("conecto_push_quotes", "--apply", stdout=out)
        self.assertEqual(self.bodies(request, "quoteCreated")[-1]["status"], "expired")
        self.assertEqual(ConectoQuote.objects.get().pushed_status, "expired")

    @patch("conecto.client.requests.request")
    def test_quote_pdf_link(self, request):
        quote = self._quote(request)
        res = post("/api/conecto/documents/quote-url/", {"quote_number": quote.number})
        self.assertEqual(res.status_code, 200, res.data)
        self.assertTrue(res.data["pdf_url"].startswith("https://portal.skybre.co.za/api/public/conecto-document/"))
        self.assertEqual(post("/api/conecto/documents/quote-url/", {"quote_number": "QUO-404"}).status_code, 404)


@CONFIGURED
class SalesSummaryTests(TestCase):
    def setUp(self):
        self.lani = User.objects.create_user(username="lani", password="x", role="sales", first_name="Lani")
        self.rudi = User.objects.create_user(username="rudi", password="x", role="admin", first_name="Rudolph")
        self.lani.refresh_from_db()
        tariff = Tariff.objects.create(name="20 Mbps", price=Decimal("500.00"))
        today = timezone.localdate()
        Lead.objects.create(full_name="A", assigned_to=self.lani, interested_tariff=tariff, status="qualified",
                            next_follow_up=today - datetime.timedelta(days=1))
        Lead.objects.create(full_name="B", assigned_to=self.lani, interested_tariff=tariff, status="won")
        Lead.objects.create(full_name="C", assigned_to=self.lani, status="lost", lost_reason="no_los")
        Lead.objects.create(full_name="D", assigned_to=self.rudi, interested_tariff=tariff, status="new")
        self.today = today

    def ask(self, code):
        return post("/api/conecto/sales/summary/", {"staff_code": code, "date_from": str(self.today),
                                                    "date_to": str(self.today)})

    def test_one_person(self):
        res = self.ask(self.lani.staff_code)
        self.assertEqual(res.status_code, 200, res.data)
        t = res.data["totals"]
        self.assertEqual((t["new_leads"], t["open_leads"], t["open_value"], t["accepted"], t["lost"]),
                         (3, 1, 500.0, 1, 1))
        self.assertEqual(t["conversion_rate"], 50.0)
        self.assertEqual(t["follow_ups_due"], 1)
        self.assertEqual(res.data["follow_ups"][0]["lead_name"], "A")
        self.assertEqual(res.data["lost_by_reason"][0]["reason"], "No line of sight")
        self.assertEqual(res.data["lost_by_stage"][0]["key"], "new")
        self.assertNotIn("by_person", res.data)

    def test_whole_team(self):
        res = self.ask(None)
        self.assertEqual(res.data["totals"]["new_leads"], 4)
        names = sorted(p["name"] for p in res.data["by_person"])
        self.assertEqual(names, ["Lani", "Rudolph"])

    def test_bad_requests(self):
        self.assertEqual(self.ask("STF-999999").status_code, 404)
        self.assertEqual(post("/api/conecto/sales/summary/", {"date_from": "x", "date_to": "y"}).status_code, 400)
