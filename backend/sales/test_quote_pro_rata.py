"""Pro rata on a lead's quote (Rudolph, 7 Oct 2026: clients start halfway
into the month, are charged from that date to month end, then get
recurring billing)."""
import datetime
from decimal import Decimal

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User
from billing.models import Tariff
from sales.models import Lead
from sales.quote import render_quote_pdf
from sales.workflow import pro_rata


class ProRataRuleTests(TestCase):
    def test_rule(self):
        p = pro_rata(datetime.date(2026, 10, 15), Decimal("600.00"))
        self.assertEqual((p["amount"], p["days"], p["month_days"], p["to"]),
                         (Decimal("329.03"), 17, 31, "2026-10-31"))
        self.assertIsNone(pro_rata(datetime.date(2026, 11, 1), Decimal("600")))
        self.assertIsNone(pro_rata(None, Decimal("600")))
        self.assertEqual(pro_rata(datetime.date(2028, 2, 29), Decimal("290"))["amount"], Decimal("10.00"))


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class QuoteProRataTests(TestCase):
    def setUp(self):
        admin = User.objects.create_user(username="boss", password="x", role="admin")
        self.api = APIClient()
        self.api.force_authenticate(admin)
        self.tariff = Tariff.objects.create(name="20 Mbps", price=Decimal("500.00"), tax_rate_pct=Decimal("15"))
        self.lead = Lead.objects.create(full_name="Sarie Marais", coverage_method="electronic", coverage_result="pass",
                                        status="coverage")

    def quote(self, start):
        return self.api.post(f"/api/leads/{self.lead.pk}/quote/", {
            "tariff": self.tariff.pk, "start_date": start,
            "recurring": [{"label": "IP Rental", "amount": "100.00"}],
        }, format="json")

    def test_mid_month(self):
        res = self.quote("2026-10-15")
        self.assertEqual(res.status_code, 200, res.data)
        t = res.data["lead"]["quote_totals"]
        self.assertEqual((t["pro_rata"]["excl"], t["pro_rata"]["incl"]), ("329.03", "378.38"))
        self.assertEqual(t["pro_rata_period"]["days"], 17)
        self.assertEqual(res.data["lead"]["quote_start_date"], "2026-10-15")
        self.lead.refresh_from_db()
        self.assertTrue(render_quote_pdf(self.lead).startswith(b"%PDF"))
        res = self.api.post(f"/api/leads/{self.lead.pk}/accept/", {}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.lead.refresh_from_db()
        self.assertIn("pro rata R 378.38", self.lead.install_job.description)

    def test_first_of_month_and_none(self):
        t = self.quote("2026-11-01").data["lead"]["quote_totals"]
        self.assertIsNone(t["pro_rata_period"])
        self.assertEqual(t["pro_rata"]["incl"], "0.00")
        self.assertIsNone(self.quote("").data["lead"]["quote_start_date"])
        self.assertEqual(self.quote("15 Oct").status_code, 400)
