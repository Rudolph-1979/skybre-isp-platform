"""Quote discounts and the unsaved preview (Rudolph, 7 Oct 2026: "Quotes
must be previewed before saving the quote" / "a option to add discount")."""
from decimal import Decimal

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User
from billing.models import Tariff
from sales.models import Lead


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class QuoteDiscountTests(TestCase):
    def setUp(self):
        admin = User.objects.create_user(username="boss", password="x", role="admin")
        self.api = APIClient()
        self.api.force_authenticate(admin)
        self.tariff = Tariff.objects.create(name="20 Mbps", price=Decimal("500.00"), tax_rate_pct=Decimal("15"))
        self.lead = Lead.objects.create(full_name="Sarie Marais", coverage_method="electronic", coverage_result="pass",
                                        status="coverage")

    def body(self, **discount):
        return {
            "tariff": self.tariff.pk, "start_date": "2026-10-15",
            "recurring": [{"label": "IP Rental", "amount": "100.00"}],
            "items": [{"label": "Installation", "amount": "1500.00"}],
            "discount": discount or {
                "monthly": {"type": "percentage", "value": "10", "months": 3},
                "once_off": {"type": "amount", "value": "200"},
                "reason": "Launch special",
            },
        }

    def test_discounted_totals(self):
        res = self.api.post(f"/api/leads/{self.lead.pk}/quote/", self.body(), format="json")
        self.assertEqual(res.status_code, 200, res.data)
        t = res.data["lead"]["quote_totals"]
        self.assertEqual(t["monthly"]["incl"], "632.50")
        self.assertEqual(t["discount"]["monthly"]["excl"], "50.00")
        self.assertEqual(t["discount"]["monthly_after"]["incl"], "690.00")
        self.assertEqual((t["discount"]["months"], t["discount"]["reason"]), (3, "Launch special"))
        self.assertEqual(t["once_off"]["incl"], "1495.00")
        self.assertEqual(t["pro_rata"]["incl"], "346.85")
        self.assertEqual(Decimal(res.data["lead"]["value"]), Decimal("550.00"))
        self.assertEqual(self.api.get(f"/api/leads/{self.lead.pk}/quote-pdf/").status_code, 200)

    def test_refusals(self):
        for discount in (
            {"monthly": {"type": "amount", "value": "600"}},
            {"monthly": {"type": "percentage", "value": "120"}},
            {"once_off": {"type": "amount", "value": "2000"}},
            {"monthly": {"type": "free", "value": "1"}},
            {"monthly": {"type": "percentage", "value": "10", "months": 99}},
        ):
            res = self.api.post(f"/api/leads/{self.lead.pk}/quote/", self.body(**discount), format="json")
            self.assertEqual(res.status_code, 400, discount)

    def test_preview_saves_nothing(self):
        res = self.api.post(f"/api/leads/{self.lead.pk}/quote-preview/", self.body(), format="json")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Type"], "application/pdf")
        self.lead.refresh_from_db()
        self.assertEqual((self.lead.status, self.lead.quote_sent_at, self.lead.quote_items), ("coverage", None, []))
        self.assertFalse(self.lead.lead_notes.exists())
        bad = self.api.post(f"/api/leads/{self.lead.pk}/quote-preview/", {"tariff": None}, format="json")
        self.assertEqual(bad.status_code, 400)
        self.assertIn("plan", bad.data["detail"])
