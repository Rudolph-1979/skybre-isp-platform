"""Several partners on one client: a partner per service (Rudolph, 5 Oct 2026)."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.testing import ALL_SECTIONS
from billing.models import Invoice, Service, Tariff
from customers.models import Customer, Partner

User = get_user_model()


class ServicePartnerTests(TestCase):
    def setUp(self):
        self.mtd = Partner.objects.create(name="MTD")
        self.vuma = Partner.objects.create(name="Vumatel")
        self.mtd_plan = Tariff.objects.create(name="MTD 250", price=Decimal("250"), speed_download_kbps=1, speed_upload_kbps=1, partner=self.mtd)
        self.vuma_plan = Tariff.objects.create(name="Vuma 50", price=Decimal("500"), speed_download_kbps=1, speed_upload_kbps=1, partner=self.vuma)
        self.client_rec = Customer.objects.create(full_name="Two Lines", partner=self.mtd)
        self.admin = User.objects.create_user(username="admin", password="x", role="admin")
        self.api = APIClient()
        self.api.force_authenticate(self.admin)

    def _new_service(self, tariff, partner=None, user=None, expect=201):
        if user:
            self.api.force_authenticate(user)
        payload = {"customer": self.client_rec.id, "tariff": tariff.id, "status": "pending"}
        if partner is not None:
            payload["partner"] = partner
        res = self.api.post("/api/services/", payload, format="json")
        self.assertEqual(res.status_code, expect, res.data)
        return res

    def test_line_inherits_client_partner(self):
        res = self._new_service(self.mtd_plan)
        self.assertIsNone(res.data["partner"])
        self.assertEqual(res.data["partner_name"], "MTD")
        self.assertEqual(res.data["effective_partner"], self.mtd.id)
        # Another partner's plan on an inheriting line is still refused.
        self._new_service(self.vuma_plan, expect=400)

    def test_line_with_its_own_partner_takes_that_partners_plans(self):
        res = self._new_service(self.vuma_plan, partner=self.vuma.id)
        self.assertEqual(res.data["partner_name"], "Vumatel")
        self._new_service(self.mtd_plan, partner=self.vuma.id, expect=400)

    def test_changing_line_partner_rechecks_its_plan(self):
        res = self._new_service(self.mtd_plan)
        bad = self.api.patch(f"/api/services/{res.data['id']}/", {"partner": self.vuma.id}, format="json")
        self.assertEqual(bad.status_code, 400)
        self.assertIn("tariff", bad.data)
        ok = self.api.patch(f"/api/services/{res.data['id']}/", {"partner": self.vuma.id, "tariff": self.vuma_plan.id}, format="json")
        self.assertEqual(ok.status_code, 200, ok.data)

    def test_customer_list_shows_all_partners_and_filters_on_them(self):
        Service.objects.create(customer=self.client_rec, tariff=self.vuma_plan, partner=self.vuma, status="active", start_date="2026-08-01")
        row = self.api.get("/api/customers/?page_size=50").data["results"][0]
        self.assertEqual(row["partner_names"], ["MTD", "Vumatel"])
        ids = [r["id"] for r in self.api.get(f"/api/customers/?partner_in={self.vuma.id}").data["results"]]
        self.assertEqual(ids, [self.client_rec.id])

    def test_restricted_user_sees_whole_client_through_a_line(self):
        Service.objects.create(customer=self.client_rec, tariff=self.vuma_plan, partner=self.vuma, status="active", start_date="2026-08-01")
        inv = Invoice.objects.create(customer=self.client_rec, status="draft", date_due="2026-10-31")
        vuma_only = User.objects.create_user(username="v", password="x", role="support",
                                             allowed_sections=ALL_SECTIONS, allowed_partners=[self.vuma.id])
        self.api.force_authenticate(vuma_only)
        names = [r["full_name"] for r in self.api.get("/api/customers/").data["results"]]
        self.assertEqual(names, ["Two Lines"])
        inv_ids = [r["id"] for r in self.api.get("/api/invoices/?page_size=100").data["results"]]
        self.assertIn(inv.id, inv_ids)
        # Saving the client unchanged is allowed even though MTD isn't theirs.
        res = self.api.patch(f"/api/customers/{self.client_rec.id}/", {"partner": self.mtd.id, "phone": "082"}, format="json")
        self.assertEqual(res.status_code, 200, res.data)

    def test_terminated_line_doesnt_grant_visibility(self):
        Service.objects.create(customer=self.client_rec, tariff=self.vuma_plan, partner=self.vuma, status="terminated", start_date="2026-08-01")
        vuma_only = User.objects.create_user(username="v", password="x", role="support",
                                             allowed_sections=ALL_SECTIONS, allowed_partners=[self.vuma.id])
        self.api.force_authenticate(vuma_only)
        self.assertEqual(self.api.get("/api/customers/").data["results"], [])

    def test_restricted_user_cannot_put_a_line_with_a_partner_they_lack(self):
        mtd_only = User.objects.create_user(username="m", password="x", role="support",
                                            allowed_sections=ALL_SECTIONS, allowed_partners=[self.mtd.id])
        res = self._new_service(self.vuma_plan, partner=self.vuma.id, user=mtd_only, expect=400)
        self.assertIn("partner", res.data)
