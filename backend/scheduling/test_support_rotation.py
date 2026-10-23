"""Weekly support rotations (#184, 16 Oct 2026).

Rudolph: "2 is on vehicle support and one is on phone support". Example
used throughout: Vehicle support, 2 per week, members Christo, Fanie,
Bernard, Elone; Phone support, 1 per week, Jayden and Lucille. Handover
Monday 08:00, first week Mon 28 Sep 2026.
"""
import datetime
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from scheduling import rotation as rot
from scheduling.models import SupportRotation

User = get_user_model()
W0 = datetime.date(2026, 9, 28)  # a Monday


def d(s):
    return datetime.date.fromisoformat(s)


class SupportRotationTests(TestCase):
    def setUp(self):
        self.office = User.objects.create_user(
            username="sched", password="pw-for-tests-123", role="support", allowed_sections=["scheduling"],
        )
        self.api = APIClient()
        self.api.force_authenticate(self.office)
        mk = lambda u, f: User.objects.create_user(username=u, password="pw-for-tests-123", role="technician",
                                                   first_name=f, phone=f"07{len(f)}")
        self.christo, self.fanie = mk("christo", "Christo"), mk("fanie", "Fanie")
        self.bernard, self.elone = mk("bernard", "Bernard"), mk("elone", "Elone")
        self.jayden = User.objects.create_user(username="jayden", password="pw-for-tests-123", role="support", first_name="Jayden")
        self.lucille = User.objects.create_user(username="lucille", password="pw-for-tests-123", role="support", first_name="Lucille")

    def create(self, name, k, members, **extra):
        body = {"name": name, "people_per_week": k, "handover_weekday": 0, "handover_time": "08:00",
                "first_week_start": W0.isoformat(), "member_ids": [m.pk for m in members], **extra}
        res = self.api.post("/api/support-rotations/", body, format="json")
        self.assertEqual(res.status_code, 201, res.data)
        return SupportRotation.objects.get(pk=res.data["id"])

    def vehicle(self):
        return self.create("Vehicle support", 2, [self.christo, self.fanie, self.bernard, self.elone])

    def phone(self):
        return self.create("Phone support", 1, [self.jayden, self.lucille])

    def names(self, rotation, week):
        members, overrides = rot._load(rotation)
        return [p["name"] for p in rot.week_entry(rotation, members, overrides, d(week))["people"]]

    # --- who is on --------------------------------------------------------------

    def test_pairs_and_singles_cycle_in_order(self):
        v, p = self.vehicle(), self.phone()
        self.assertEqual(self.names(v, "2026-09-28"), ["Christo", "Fanie"])
        self.assertEqual(self.names(v, "2026-10-05"), ["Bernard", "Elone"])
        self.assertEqual(self.names(v, "2026-10-12"), ["Christo", "Fanie"])
        self.assertEqual([self.names(p, w)[0] for w in ("2026-09-28", "2026-10-05", "2026-10-12")],
                         ["Jayden", "Lucille", "Jayden"])

    def test_odd_member_count_mixes_the_pairs(self):
        v = self.create("Vehicle support", 2, [self.christo, self.fanie, self.bernard])
        self.assertEqual(self.names(v, "2026-09-28"), ["Christo", "Fanie"])
        self.assertEqual(self.names(v, "2026-10-05"), ["Bernard", "Christo"])
        self.assertEqual(self.names(v, "2026-10-12"), ["Fanie", "Bernard"])

    def test_nobody_before_the_first_week(self):
        v = self.vehicle()
        self.assertEqual(self.names(v, "2026-09-21"), [])

    def test_schedule_endpoint_covers_the_month_grid(self):
        self.vehicle(); self.phone()
        res = self.api.get("/api/support-rotations/schedule/?from=2026-09-27&to=2026-11-07")
        self.assertEqual(res.status_code, 200, res.data)
        weeks = res.data["weeks"]
        # 28 Sep .. 2 Nov = 6 weeks per rotation.
        self.assertEqual(len(weeks), 12)
        first = [w for w in weeks if w["rotation_name"] == "Vehicle support"][0]
        self.assertEqual(first["week_start"], "2026-09-28")
        self.assertEqual(first["week_end"], "2026-10-05")
        self.assertEqual(first["people"][0]["phone"], "077")

    def test_now_honours_the_handover_time(self):
        v = self.vehicle()
        tz = timezone.get_current_timezone()
        before = datetime.datetime(2026, 10, 12, 7, 59, tzinfo=tz)   # Mon 07:59 -> still last week
        after = datetime.datetime(2026, 10, 12, 8, 0, tzinfo=tz)
        self.assertEqual(rot.now_week_start(v, before), d("2026-10-05"))
        self.assertEqual(rot.now_week_start(v, after), d("2026-10-12"))

    def test_now_endpoint(self):
        self.vehicle(); self.phone()
        tz = timezone.get_current_timezone()
        with mock.patch("django.utils.timezone.now", return_value=datetime.datetime(2026, 10, 16, 12, 0, tzinfo=tz)):
            res = self.api.get("/api/support-rotations/now/")
        self.assertEqual(res.status_code, 200)
        by = {r["rotation_name"]: r for r in res.data["rotations"]}
        self.assertEqual([p["name"] for p in by["Vehicle support"]["current"]["people"]], ["Christo", "Fanie"])
        self.assertEqual([p["name"] for p in by["Vehicle support"]["next"]["people"]], ["Bernard", "Elone"])
        self.assertEqual([p["name"] for p in by["Phone support"]["current"]["people"]], ["Jayden"])

    # --- cover and swap -----------------------------------------------------------

    def test_cover_changes_only_that_person_that_week(self):
        v = self.vehicle()
        res = self.api.post(f"/api/support-rotations/{v.pk}/cover/", {
            "week_start": "2026-10-12", "user": self.fanie.pk, "replacement": self.bernard.pk, "reason": "Leave",
        }, format="json")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(self.names(v, "2026-10-12"), ["Christo", "Bernard"])
        self.assertEqual(self.names(v, "2026-10-19"), ["Bernard", "Elone"])
        self.assertEqual(self.names(v, "2026-10-26"), ["Christo", "Fanie"])

    def test_swap_gives_the_other_persons_next_week_back(self):
        p = self.phone()
        res = self.api.post(f"/api/support-rotations/{p.pk}/swap/", {
            "week_start": "2026-10-19", "user": self.lucille.pk, "with_user": self.jayden.pk,
        }, format="json")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["swapped_back_week"], "2026-10-26")
        self.assertEqual(self.names(p, "2026-10-19"), ["Jayden"])
        self.assertEqual(self.names(p, "2026-10-26"), ["Lucille"])
        self.assertEqual(self.names(p, "2026-11-02"), ["Lucille"])

    def test_undoing_one_side_of_a_swap_undoes_both(self):
        p = self.phone()
        self.api.post(f"/api/support-rotations/{p.pk}/swap/", {
            "week_start": "2026-10-19", "user": self.lucille.pk, "with_user": self.jayden.pk,
        }, format="json")
        oid = p.overrides.first().pk
        res = self.api.delete(f"/api/support-rotations/{p.pk}/overrides/{oid}/")
        self.assertEqual(res.status_code, 204)
        self.assertEqual(p.overrides.count(), 0)
        self.assertEqual(self.names(p, "2026-10-19"), ["Lucille"])

    def test_cover_refusals(self):
        v = self.vehicle()
        url = f"/api/support-rotations/{v.pk}/cover/"
        # Not on that week.
        self.assertEqual(self.api.post(url, {"week_start": "2026-10-12", "user": self.bernard.pk,
                                             "replacement": self.elone.pk}, format="json").status_code, 400)
        # Replacement already on.
        self.assertEqual(self.api.post(url, {"week_start": "2026-10-12", "user": self.fanie.pk,
                                             "replacement": self.christo.pk}, format="json").status_code, 400)
        # Not a week start.
        self.assertEqual(self.api.post(url, {"week_start": "2026-10-13", "user": self.fanie.pk,
                                             "replacement": self.bernard.pk}, format="json").status_code, 400)

    # --- setup validation ------------------------------------------------------------

    def test_first_week_must_be_on_the_handover_day(self):
        res = self.api.post("/api/support-rotations/", {
            "name": "Phone support", "people_per_week": 1, "handover_weekday": 0,
            "first_week_start": "2026-09-29", "member_ids": [self.jayden.pk],
        }, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertIn("first_week_start", res.data)

    def test_reordering_members_replans(self):
        p = self.phone()
        res = self.api.patch(f"/api/support-rotations/{p.pk}/", {"member_ids": [self.lucille.pk, self.jayden.pk]},
                             format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual([m["name"] for m in res.data["members"]], ["Lucille", "Jayden"])
        self.assertEqual(self.names(p, "2026-09-28"), ["Lucille"])

    def test_customer_cannot_be_a_member_and_no_access_without_scheduling(self):
        cust = User.objects.create_user(username="cust", password="pw-for-tests-123", role="customer")
        res = self.api.post("/api/support-rotations/", {
            "name": "Phone support", "people_per_week": 1, "handover_weekday": 0,
            "first_week_start": W0.isoformat(), "member_ids": [cust.pk],
        }, format="json")
        self.assertEqual(res.status_code, 400)
        other = APIClient()
        other.force_authenticate(User.objects.create_user(
            username="nosched", password="pw-for-tests-123", role="support", allowed_sections=["tickets"]))
        self.assertEqual(other.get("/api/support-rotations/").status_code, 403)
