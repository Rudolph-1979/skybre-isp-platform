"""Paying / Sponsor / Deal with a reason (Rudolph, 6 Oct 2026)."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from customers.models import Customer

User = get_user_model()


class AccountTypeTests(TestCase):
    def setUp(self):
        self.api = APIClient()
        self.api.force_authenticate(User.objects.create_user(username="a", password="x", role="admin"))

    def test_default_is_paying(self):
        res = self.api.post("/api/customers/", {"full_name": "Plain"}, format="json")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["account_type"], "paying")
        self.assertEqual(res.data["account_type_reason"], "")

    def test_sponsor_needs_a_reason(self):
        res = self.api.post("/api/customers/", {"full_name": "Club", "account_type": "sponsor"}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertIn("account_type_reason", res.data)
        res = self.api.post(
            "/api/customers/",
            {"full_name": "Club", "account_type": "sponsor", "account_type_reason": "  Rugby club sponsorship "},
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["account_type_reason"], "Rugby club sponsorship")

    def test_back_to_paying_drops_the_reason(self):
        c = Customer.objects.create(full_name="Deal", account_type="deal", account_type_reason="3 months half price")
        res = self.api.patch(f"/api/customers/{c.id}/", {"account_type": "paying"}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        c.refresh_from_db()
        self.assertEqual(c.account_type_reason, "")

    def test_editing_other_fields_leaves_it_alone(self):
        c = Customer.objects.create(full_name="Deal", account_type="deal", account_type_reason="Launch deal")
        res = self.api.patch(f"/api/customers/{c.id}/", {"phone": "082"}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        c.refresh_from_db()
        self.assertEqual((c.account_type, c.account_type_reason), ("deal", "Launch deal"))

    def test_filter_and_sort(self):
        Customer.objects.create(full_name="A", account_type="sponsor", account_type_reason="x")
        Customer.objects.create(full_name="B")
        names = [r["full_name"] for r in self.api.get("/api/customers/?account_type=sponsor").data["results"]]
        self.assertEqual(names, ["A"])
        res = self.api.get("/api/customers/?ordering=account_type")
        self.assertEqual(res.status_code, 200)


class EditableAccountTypeTests(TestCase):
    """The list is editable (6 Oct 2026: "Add a option to add more options,
    one in mind is Staff")."""

    def setUp(self):
        self.api = APIClient()
        self.api.force_authenticate(User.objects.create_user(username="a", password="x", role="admin"))

    def test_seeded_with_staff(self):
        keys = [r["key"] for r in self.api.get("/api/account-types/").data]
        self.assertEqual(keys, ["paying", "sponsor", "deal", "staff"])

    def test_staff_needs_no_reason(self):
        res = self.api.post("/api/customers/", {"full_name": "Fanie", "account_type": "staff"}, format="json")
        self.assertEqual(res.status_code, 201, res.data)

    def test_add_a_type_and_use_it(self):
        res = self.api.post("/api/account-types/", {"name": "Family", "needs_reason": True, "colour": "#C03C78"}, format="json")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["key"], "family")
        bad = self.api.post("/api/customers/", {"full_name": "Mom", "account_type": "family"}, format="json")
        self.assertEqual(bad.status_code, 400)
        ok = self.api.post("/api/customers/", {"full_name": "Mom", "account_type": "family", "account_type_reason": "Owner's mother"}, format="json")
        self.assertEqual(ok.status_code, 201, ok.data)
        counts = {r["key"]: r["customer_count"] for r in self.api.get("/api/account-types/").data}
        self.assertEqual(counts["family"], 1)

    def test_unknown_or_switched_off_type_refused(self):
        self.assertEqual(self.api.post("/api/customers/", {"full_name": "X", "account_type": "nope"}, format="json").status_code, 400)
        from customers.models import ClientAccountType
        deal = ClientAccountType.objects.get(key="deal")
        self.api.patch(f"/api/account-types/{deal.id}/", {"is_active": False}, format="json")
        res = self.api.post("/api/customers/", {"full_name": "X", "account_type": "deal", "account_type_reason": "y"}, format="json")
        self.assertEqual(res.status_code, 400)

    def test_delete_rules(self):
        from customers.models import ClientAccountType
        paying = ClientAccountType.objects.get(key="paying")
        self.assertEqual(self.api.delete(f"/api/account-types/{paying.id}/").status_code, 400)
        self.assertEqual(self.api.patch(f"/api/account-types/{paying.id}/", {"is_active": False}, format="json").status_code, 400)
        Customer.objects.create(full_name="On deal", account_type="deal", account_type_reason="x")
        deal = ClientAccountType.objects.get(key="deal")
        self.assertEqual(self.api.delete(f"/api/account-types/{deal.id}/").status_code, 400)
        staff = ClientAccountType.objects.get(key="staff")
        self.assertEqual(self.api.delete(f"/api/account-types/{staff.id}/").status_code, 204)

    def test_rename_keeps_key(self):
        from customers.models import ClientAccountType
        deal = ClientAccountType.objects.get(key="deal")
        res = self.api.patch(f"/api/account-types/{deal.id}/", {"name": "Special deal"}, format="json")
        self.assertEqual(res.data["key"], "deal")
