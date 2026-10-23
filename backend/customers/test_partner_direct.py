"""Partner access with the "Direct" tick (Rudolph, 5 Oct 2026).

A restricted user sees only the partners ticked for them, and direct
customers (no partner) only when "Direct" (0 in allowed_partners) is ticked.
Also settable from Configs -> Users -> Edit user (StaffAccountsSerializer).
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from customers.models import Customer, Partner
from customers.scoping import DIRECT

User = get_user_model()
ALL_SECTIONS = list(User.Section.values)


class DirectTickTests(TestCase):
    def setUp(self):
        self.acme = Partner.objects.create(name="Acme")
        self.rival = Partner.objects.create(name="Rival")
        Customer.objects.create(full_name="Acme Customer", partner=self.acme)
        Customer.objects.create(full_name="Rival Customer", partner=self.rival)
        Customer.objects.create(full_name="Direct Customer")
        self.client = APIClient()

    def _user(self, allowed, role="support", name="u"):
        return User.objects.create_user(
            username=name, password="pw-for-tests-123", role=role,
            allowed_partners=allowed, allowed_sections=ALL_SECTIONS,
        )

    def _names(self, user):
        self.client.force_authenticate(user)
        res = self.client.get("/api/customers/?page_size=100")
        self.assertEqual(res.status_code, 200)
        return {r["full_name"] for r in res.data["results"]}

    def test_partner_only_hides_direct(self):
        self.assertEqual(self._names(self._user([self.acme.id])), {"Acme Customer"})

    def test_partner_plus_direct(self):
        self.assertEqual(self._names(self._user([self.acme.id, DIRECT])), {"Acme Customer", "Direct Customer"})

    def test_direct_only(self):
        self.assertEqual(self._names(self._user([DIRECT])), {"Direct Customer"})

    def test_unrestricted_and_admin_see_all(self):
        everyone = {"Acme Customer", "Rival Customer", "Direct Customer"}
        self.assertEqual(self._names(self._user([], name="hq")), everyone)
        self.assertEqual(self._names(self._user([self.acme.id], role="admin", name="boss")), everyone)

    def test_create_respects_access(self):
        user = self._user([self.acme.id])
        self.client.force_authenticate(user)
        res = self.client.post("/api/customers/", {"full_name": "New Direct", "email": "n@x.com"}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertIn("partner", res.data)
        res = self.client.post("/api/customers/", {"full_name": "New Rival", "partner": self.rival.id}, format="json")
        self.assertEqual(res.status_code, 400)
        res = self.client.post("/api/customers/", {"full_name": "New Acme", "partner": self.acme.id}, format="json")
        self.assertEqual(res.status_code, 201, res.data)

        user.allowed_partners = [self.acme.id, DIRECT]
        user.save(update_fields=["allowed_partners"])
        res = self.client.post("/api/customers/", {"full_name": "New Direct"}, format="json")
        self.assertEqual(res.status_code, 201, res.data)

    def test_edit_cannot_move_customer_to_hidden_partner(self):
        user = self._user([self.acme.id])
        mine = Customer.objects.get(full_name="Acme Customer")
        self.client.force_authenticate(user)
        res = self.client.patch(f"/api/customers/{mine.id}/", {"partner": None}, format="json")
        self.assertEqual(res.status_code, 400)
        res = self.client.patch(f"/api/customers/{mine.id}/", {"phone": "0820000000"}, format="json")
        self.assertEqual(res.status_code, 200, res.data)

    def test_edit_user_sets_partner_access(self):
        admin = self._user([], role="admin", name="admin")
        staff = self._user([], name="staff")
        self.client.force_authenticate(admin)
        res = self.client.patch(
            f"/api/staff-accounts/{staff.id}/",
            {"allowed_partners": [DIRECT, self.acme.id, self.acme.id]}, format="json",
        )
        self.assertEqual(res.status_code, 200, res.data)
        staff.refresh_from_db()
        self.assertEqual(staff.allowed_partners, [DIRECT, self.acme.id])
        res = self.client.patch(f"/api/staff-accounts/{staff.id}/", {"allowed_partners": [99999]}, format="json")
        self.assertEqual(res.status_code, 400)

    def test_permissions_screen_accepts_direct(self):
        admin = self._user([], role="admin", name="admin")
        staff = self._user([], name="staff")
        self.client.force_authenticate(admin)
        res = self.client.patch(f"/api/staff-permissions/{staff.id}/", {"allowed_partners": [DIRECT]}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
