"""The quoted plan is priced at the tariff, not at an estimate typed on the
lead earlier (Rudolph, 9 Oct 2026: a quote showed "Skybre Wireless 4 Mbps
(monthly) R 0.00")."""
from decimal import Decimal

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User
from billing.models import Tariff
from sales.models import Lead


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class QuotePlanPriceTests(TestCase):
    def setUp(self):
        self.api = APIClient()
        self.api.force_authenticate(User.objects.create_user(username="boss", password="x", role="admin"))
        self.tariff = Tariff.objects.create(name="Skybre Wireless 4 Mbps", price=Decimal("150.00"),
                                            tax_rate_pct=Decimal("15"))
        self.lead = Lead.objects.create(full_name="Lucky Moeletsi", coverage_method="electronic",
                                        coverage_result="pass", status="coverage",
                                        estimated_monthly_value=Decimal("0.00"))

    def body(self):
        return {"tariff": self.tariff.pk, "items": [{"label": "Rental Install", "amount": "391.30"}]}

    def test_preview_and_saved_quote_use_the_tariff_price(self):
        preview = self.api.post(f"/api/leads/{self.lead.pk}/quote-preview/", self.body(), format="json")
        self.assertEqual(preview.status_code, 200)
        res = self.api.post(f"/api/leads/{self.lead.pk}/quote/", self.body(), format="json")
        self.assertEqual(res.status_code, 200, res.data)
        totals = res.data["lead"]["quote_totals"]
        self.assertEqual(totals["plan"]["excl"], "150.00")
        self.assertEqual(totals["monthly"]["incl"], "172.50")
        self.lead.refresh_from_db()
        self.assertIsNone(self.lead.estimated_monthly_value)
        self.assertEqual(self.lead.plan_value, Decimal("150.00"))

    def test_free_month_is_a_discount_not_a_zero_price(self):
        body = {**self.body(), "discount": {"monthly": {"type": "percentage", "value": "100", "months": 1}}}
        res = self.api.post(f"/api/leads/{self.lead.pk}/quote/", body, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        totals = res.data["lead"]["quote_totals"]
        self.assertEqual(totals["plan_gross"]["excl"], "150.00")
        self.assertEqual(totals["plan"]["excl"], "0.00")


class QuoteCalculationTests(TestCase):
    """"How this is calculated" on the quote (9 Oct 2026)."""

    def test_lines_spell_out_the_sums(self):
        from sales.quote import calculation_lines
        from sales.workflow import quote_totals

        tariff = Tariff.objects.create(name="Skybre Wireless 4 Mbps", price=Decimal("149.00"),
                                       tax_rate_pct=Decimal("15"))
        lead = Lead.objects.create(full_name="Lucky Moeletsi", interested_tariff=tariff,
                                   quote_recurring=[{"label": "Public IP Rental", "amount": "15.00"},
                                                    {"label": "Renting of Equipment", "amount": "150.00"}],
                                   quote_items=[{"label": "Rental Install", "amount": "391.30"}])
        lines = calculation_lines(lead, quote_totals(lead))
        self.assertEqual(lines[0], "Monthly: Skybre Wireless 4 Mbps R 149.00 + Public IP Rental R 15.00 + "
                                   "Renting of Equipment R 150.00 = R 314.00 excl. VAT; + VAT 15% (R 47.10) = "
                                   "R 361.10 incl. VAT.")
        self.assertEqual(lines[1], "Once-off: Rental Install R 391.30 = R 391.30 excl. VAT; + VAT 15% (R 58.70) = "
                                   "R 450.00 incl. VAT.")
        from sales.quote import render_quote_pdf

        self.assertTrue(render_quote_pdf(lead).startswith(b"%PDF"))


class QuoteFirstMonthTests(TestCase):
    """Next to each monthly line: days, per day, and its first-month share
    (9 Oct 2026)."""

    def test_each_monthly_line_gets_its_share_and_they_add_up(self):
        import datetime
        from unittest import mock

        from sales.quote import render_quote_pdf

        tariff = Tariff.objects.create(name="Skybre Wireless 4 Mbps", price=Decimal("149.00"),
                                       tax_rate_pct=Decimal("15"))
        lead = Lead.objects.create(full_name="Lucky Moeletsi", interested_tariff=tariff,
                                   quote_start_date=datetime.date(2026, 10, 11),
                                   quote_recurring=[{"label": "Public IP Rental", "amount": "15.00"},
                                                    {"label": "Renting of Equipment", "amount": "150.00"}],
                                   quote_items=[{"label": "Rental Install", "amount": "391.30"}])
        captured = {}

        def fake(template, ctx):
            captured.update(ctx)
            return b"%PDF"

        with mock.patch("notifications.pdf._render_pdf", side_effect=fake):
            render_quote_pdf(lead)
        monthly = [i for i in captured["items"] if i.get("first_month")]
        self.assertEqual([i["first_month"] for i in monthly], [
            "21 days (R 4.81 per day)", "21 days (R 0.48 per day)", "21 days (R 4.84 per day)"])
        self.assertEqual([i["first_month_amount"] for i in monthly], ["100.94", "10.16", "101.61"])
        self.assertEqual(sum(Decimal(i["first_month_amount"]) for i in monthly),
                         captured["totals"]["pro_rata"]["excl"])
        self.assertFalse(any(i["label"].startswith("Pro rata") for i in captured["items"]))
        self.assertTrue(any(line.startswith("Pro rata: R 314.00 × 21/31") for line in captured["calculation"]))
        self.assertTrue(render_quote_pdf(lead).startswith(b"%PDF"))
