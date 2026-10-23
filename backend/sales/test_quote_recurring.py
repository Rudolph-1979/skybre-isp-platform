"""Recurring charges on a lead's quote (Rudolph, 7 Oct 2026: "Tariff Plan,
Recurring Charges & Once Off Items")."""
from decimal import Decimal

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User
from billing.models import RecurringCharge, Tariff
from sales.models import Lead
from sales.quote import render_quote_pdf


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class QuoteRecurringTests(TestCase):
    def setUp(self):
        admin = User.objects.create_user(username="boss", password="x", role="admin")
        self.api = APIClient()
        self.api.force_authenticate(admin)
        self.tariff = Tariff.objects.create(name="20 Mbps", price=Decimal("500.00"), tax_rate_pct=Decimal("15"))
        self.ip = RecurringCharge.objects.create(name="IP Rental", price=Decimal("100.00"), tax_rate_pct=Decimal("15"))
        self.lead = Lead.objects.create(full_name="Sarie Marais", coverage_method="electronic", coverage_result="pass",
                                        status="coverage")

    def quote(self, **extra):
        return self.api.post(f"/api/leads/{self.lead.pk}/quote/", {
            "tariff": self.tariff.pk,
            "recurring": [{"label": "IP Rental", "amount": "100.00", "recurring_charge": self.ip.pk},
                          {"label": "", "amount": "5"}],
            "items": [{"label": "Installation", "amount": "1500.00"}],
            **extra,
        }, format="json")

    def test_monthly_is_plan_plus_recurring(self):
        res = self.quote()
        self.assertEqual(res.status_code, 200, res.data)
        lead = res.data["lead"]
        self.assertEqual(lead["quote_recurring"], [{"label": "IP Rental", "amount": "100.00",
                                                    "recurring_charge": self.ip.pk}])
        self.assertEqual(lead["quote_totals"]["monthly"]["incl"], "690.00")
        self.assertEqual(lead["quote_totals"]["plan"]["excl"], "500.00")
        self.assertEqual(lead["quote_totals"]["once_off"]["incl"], "1725.00")
        self.assertEqual(Decimal(lead["value"]), Decimal("600.00"))
        self.lead.refresh_from_db()
        self.assertTrue(render_quote_pdf(self.lead).startswith(b"%PDF"))

    def test_bad_amount_and_shape(self):
        bad = self.api.post(f"/api/leads/{self.lead.pk}/quote/", {
            "tariff": self.tariff.pk, "recurring": [{"label": "IP", "amount": "abc"}]}, format="json")
        self.assertEqual(bad.status_code, 400)
        bad = self.api.post(f"/api/leads/{self.lead.pk}/quote/", {
            "tariff": self.tariff.pk, "recurring": "IP"}, format="json")
        self.assertEqual(bad.status_code, 400)

    def test_accept_puts_extras_on_the_install_job(self):
        self.quote()
        res = self.api.post(f"/api/leads/{self.lead.pk}/accept/", {}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.lead.refresh_from_db()
        self.assertIn("Monthly extras: IP Rental R 100.00", self.lead.install_job.description)
