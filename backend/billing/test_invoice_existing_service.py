"""An invoice/quote tariff line uses the client's existing service on that
plan instead of creating a second one (#202, Rudolph 20 Oct 2026: Vossie
Voster had two Vuma BF 30/30 services -- one added on the Services tab, one
made by the pro-rata invoice raised for it)."""
import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from billing.models import Invoice, InvoiceItem, Service, Tariff
from customers.models import Customer

User = get_user_model()
D = datetime.date


class ExistingServiceTests(TestCase):
    def setUp(self):
        self.vuma = Tariff.objects.create(name="Vuma BF 30/30", price=Decimal("449.00"), tax_rate_pct=Decimal("15.00"),
                                          speed_download_kbps=30720, speed_upload_kbps=30720)
        self.wifi = Tariff.objects.create(name="Wireless 6", price=Decimal("349.00"), tax_rate_pct=Decimal("15.00"),
                                          speed_download_kbps=6144, speed_upload_kbps=6144)
        self.c = Customer.objects.create(full_name="Vossie Voster", status=Customer.Status.ACTIVE)

    def service(self, tariff, status=Service.Status.ACTIVE, **kw):
        return Service.objects.create(customer=self.c, tariff=tariff, status=status, start_date=D(2026, 10, 19), **kw)

    def doc(self, status, *tariffs):
        inv = Invoice.objects.create(customer=self.c, status=status, date_due=D(2026, 10, 26))
        for t in tariffs:
            InvoiceItem.objects.create(invoice=inv, item_type=InvoiceItem.ItemType.TARIFF, tariff=t, description=t.name,
                                       quantity=1, unit_price=t.price, tax_rate_pct=t.tax_rate_pct,
                                       period_start=D(2026, 10, 19), period_end=D(2026, 10, 31))
        inv.recalc_totals()
        return inv

    def test_invoice_uses_the_service_added_by_hand(self):
        s = self.service(self.vuma, radius_username="svf_krd_vossie_voster")
        inv = self.doc(Invoice.Status.UNPAID, self.vuma)
        inv.activate_tariff_services()
        self.assertEqual(list(self.c.services.values_list("pk", flat=True)), [s.pk])
        self.assertEqual(inv.items.get().service_id, s.pk)
        s.refresh_from_db()
        self.assertEqual(s.radius_username, "svf_krd_vossie_voster")

    def test_converted_quote_uses_it_too(self):
        s = self.service(self.vuma, status=Service.Status.PENDING)
        q = self.doc(Invoice.Status.QUOTE, self.vuma)
        q.convert_to_invoice()
        self.assertEqual(self.c.services.count(), 1)
        s.refresh_from_db()
        self.assertEqual(s.status, Service.Status.PENDING, "the service is left as staff set it up")
        self.assertEqual(q.items.get().service_id, s.pk)

    def test_invoice_raised_through_the_api(self):
        s = self.service(self.vuma)
        api = APIClient()
        api.force_authenticate(User.objects.create_user(username="boss", password="x", role="admin"))
        res = api.post("/api/invoices/", {
            "customer": self.c.pk, "status": "unpaid", "date_due": "2026-10-26",
            "items": [{"item_type": "tariff", "tariff": self.vuma.pk, "description": "Vuma BF 30/30 pro rata",
                       "quantity": 1, "unit_price": "188.29", "tax_rate_pct": "15.00",
                       "period_start": "2026-10-19", "period_end": "2026-10-31"}],
        }, format="json")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(list(self.c.services.values_list("pk", flat=True)), [s.pk])

    def test_new_service_when_none_on_that_plan(self):
        wifi = self.service(self.wifi)
        inv = self.doc(Invoice.Status.UNPAID, self.vuma)
        inv.activate_tariff_services()
        self.assertEqual(self.c.services.count(), 2)
        new = self.c.services.exclude(pk=wifi.pk).get()
        self.assertEqual((new.tariff, new.status, new.start_date), (self.vuma, Service.Status.ACTIVE, D(2026, 10, 19)))

    def test_terminated_service_is_not_reused(self):
        old = self.service(self.vuma, status=Service.Status.TERMINATED)
        inv = self.doc(Invoice.Status.UNPAID, self.vuma)
        inv.activate_tariff_services()
        self.assertEqual(self.c.services.count(), 2)
        self.assertNotEqual(inv.items.get().service_id, old.pk)

    def test_two_lines_same_plan_one_existing(self):
        s = self.service(self.vuma)
        inv = self.doc(Invoice.Status.UNPAID, self.vuma, self.vuma)
        inv.activate_tariff_services()
        linked = sorted(inv.items.values_list("service_id", flat=True))
        self.assertEqual(self.c.services.count(), 2)
        self.assertIn(s.pk, linked)
        self.assertEqual(len(set(linked)), 2)

    def test_running_twice_adds_nothing(self):
        self.service(self.vuma)
        inv = self.doc(Invoice.Status.UNPAID, self.vuma)
        inv.activate_tariff_services()
        inv.activate_tariff_services()
        self.assertEqual(self.c.services.count(), 1)
