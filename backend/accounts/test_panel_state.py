"""Foldable panels, open ones remembered per person (#193, Rudolph 16 Oct
2026: "Make the panels foldable in order to read better all the info")."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

User = get_user_model()


class PanelStateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="sam", password="x", role="support")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_starts_empty_and_is_on_me(self):
        self.assertEqual(self.client.get("/api/me/").data["panel_state"], {})

    def test_save_merge_and_reset_per_page(self):
        r = self.client.patch("/api/me/", {"panel_state": {"vehicle": ["odometer", "fuel"]}}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["panel_state"], {"vehicle": ["odometer", "fuel"]})
        # Another page leaves this one alone.
        r = self.client.patch("/api/me/", {"panel_state": {"customer": []}}, format="json")
        self.assertEqual(r.data["panel_state"], {"vehicle": ["odometer", "fuel"], "customer": []})
        # Everything folded is remembered as such, not as "default".
        r = self.client.patch("/api/me/", {"panel_state": {"vehicle": []}}, format="json")
        self.assertEqual(r.data["panel_state"]["vehicle"], [])
        # null = back to the page's default.
        r = self.client.patch("/api/me/", {"panel_state": {"vehicle": None}}, format="json")
        self.assertEqual(r.data["panel_state"], {"customer": []})

    def test_each_person_has_their_own_and_layout_is_untouched(self):
        other = User.objects.create_user(username="lee", password="x", role="support")
        self.client.patch("/api/me/", {"dashboard_layout": {"order": ["tickets_open"]}}, format="json")
        self.client.patch("/api/me/", {"panel_state": {"vehicle": ["trips"]}}, format="json")
        other.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(other.panel_state, {})
        self.assertEqual(self.user.dashboard_layout, {"order": ["tickets_open"]})

    def test_rejects_junk(self):
        for bad in ("x", {}, {"vehicle": "odometer"}, {"Vehicle!": []}, {"vehicle": ["a", "a"]},
                    {"vehicle": ["Bad Key"]}, {"vehicle": [f"k{i}" for i in range(31)]}):
            r = self.client.patch("/api/me/", {"panel_state": bad}, format="json")
            self.assertEqual(r.status_code, 400, bad)

    def test_cannot_touch_anything_else(self):
        r = self.client.patch("/api/me/", {"panel_state": {"vehicle": []}, "role": "admin"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.user.refresh_from_db()
        self.assertEqual((self.user.role, self.user.panel_state), ("support", {}))
