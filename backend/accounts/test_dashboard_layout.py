"""Dashboard tile order per person (Rudolph, 15 Oct 2026: "Can we make the
tile movable and remembered for each users profile?")."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

User = get_user_model()


class DashboardLayoutTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="sam", password="x", role="support")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_starts_empty_and_is_on_me(self):
        self.assertEqual(self.client.get("/api/me/").data["dashboard_layout"], {})

    def test_save_and_read_back(self):
        order = ["uisp_low_link", "customers_total", "tickets_open"]
        r = self.client.patch("/api/me/", {"dashboard_layout": {"order": order}}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["dashboard_layout"], {"order": order})
        self.user.refresh_from_db()
        self.assertEqual(self.user.dashboard_layout["order"], order)

    def test_each_person_has_their_own(self):
        other = User.objects.create_user(username="lee", password="x", role="support")
        self.client.patch("/api/me/", {"dashboard_layout": {"order": ["tickets_open"]}}, format="json")
        other.refresh_from_db()
        self.assertEqual(other.dashboard_layout, {})

    def test_reset_with_empty_order(self):
        self.client.patch("/api/me/", {"dashboard_layout": {"order": ["tickets_open"]}}, format="json")
        r = self.client.patch("/api/me/", {"dashboard_layout": {"order": []}}, format="json")
        self.assertEqual(r.data["dashboard_layout"], {})

    def test_rejects_junk(self):
        for bad in (
            "x",
            {"order": "tickets_open"},
            {"order": ["Bad Key!"]},
            {"order": ["a", "a"]},
            {"order": ["a"], "hidden": []},
            {"order": [f"k{i}" for i in range(101)]},
        ):
            r = self.client.patch("/api/me/", {"dashboard_layout": bad}, format="json")
            self.assertEqual(r.status_code, 400, bad)

    def test_cannot_touch_anything_else(self):
        r = self.client.patch("/api/me/", {"dashboard_layout": {"order": []}, "role": "admin"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.user.refresh_from_db()
        self.assertEqual(self.user.role, "support")

    def test_visible_partners_still_works(self):
        r = self.client.patch("/api/me/", {"visible_partners": []}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        r = self.client.patch("/api/me/", {}, format="json")
        self.assertEqual(r.status_code, 400)
