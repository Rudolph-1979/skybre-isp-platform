"""Staff calendar colours (Rudolph, 6 Oct 2026)."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import CALENDAR_PALETTE

User = get_user_model()


class CalendarColourTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="admin", password="x", role="admin")
        self.tech = User.objects.create_user(username="tech", password="x", role="technician", first_name="Christo")
        self.api = APIClient()

    def test_everyone_has_an_automatic_colour(self):
        self.api.force_authenticate(self.admin)
        rows = {r["username"]: r["calendar_colour"] for r in self.api.get("/api/staff-users/").data["results"]}
        self.assertIn(rows["tech"], CALENDAR_PALETTE)

    def test_admin_sets_it_on_edit_user(self):
        self.api.force_authenticate(self.admin)
        res = self.api.patch(f"/api/staff-accounts/{self.tech.id}/", {"calendar_colour": "#AA00CC"}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["calendar_colour"], "#aa00cc")
        self.assertEqual(res.data["display_colour"], "#aa00cc")
        bad = self.api.patch(f"/api/staff-accounts/{self.tech.id}/", {"calendar_colour": "purple"}, format="json")
        self.assertEqual(bad.status_code, 400)

    def test_scheduling_staff_change_it_from_the_calendar(self):
        planner = User.objects.create_user(username="plan", password="x", role="support", allowed_sections=["scheduling"])
        self.api.force_authenticate(planner)
        res = self.api.patch(f"/api/staff-users/{self.tech.id}/colour/", {"calendar_colour": "#112233"}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.tech.refresh_from_db()
        self.assertEqual(self.tech.calendar_colour, "#112233")
        res = self.api.patch(f"/api/staff-users/{self.tech.id}/colour/", {"calendar_colour": ""}, format="json")
        self.assertEqual(res.data["custom"], False)
        self.assertIn(res.data["calendar_colour"], CALENDAR_PALETTE)

    def test_needs_scheduling_access(self):
        other = User.objects.create_user(username="fin", password="x", role="accounts", allowed_sections=["finance"])
        self.api.force_authenticate(other)
        res = self.api.patch(f"/api/staff-users/{self.tech.id}/colour/", {"calendar_colour": "#112233"}, format="json")
        self.assertEqual(res.status_code, 403)
