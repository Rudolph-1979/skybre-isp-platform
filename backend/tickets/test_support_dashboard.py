"""The Support page (#212). See tickets.support for the definitions tested here."""
import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.testing import ALL_SECTIONS
from billing.models import Service, SuspensionExtensionRequest, Tariff
from customers.models import Customer, Partner
from network.models import Device
from radiusauth.models import RadAcct
from tickets.models import SupportSettings, Ticket, TicketComment
from tickets.support import find_outages, office_minutes, response_stats

User = get_user_model()
TZ = timezone.get_current_timezone()


def at(y, m, d, hh=0, mm=0):
    return timezone.make_aware(datetime.datetime(y, m, d, hh, mm), TZ)


class Cfg:
    office_start = datetime.time(8, 0)
    office_end = datetime.time(17, 0)
    office_days = [0, 1, 2, 3, 4]
    reply_target_minutes = 60


class OfficeMinutesTests(TestCase):
    # 22 Oct 2026 is a Thursday.
    def test_inside_one_day(self):
        self.assertEqual(office_minutes(at(2026, 10, 22, 9), at(2026, 10, 22, 10, 30), Cfg, set()), 90)

    def test_overnight_counts_only_office_time(self):
        # 17:30 Thu -> 08:10 Fri = 10 office minutes.
        self.assertEqual(office_minutes(at(2026, 10, 22, 17, 30), at(2026, 10, 23, 8, 10), Cfg, set()), 10)

    def test_weekend_skipped(self):
        # 16:00 Fri -> 09:00 Mon = 60 + 60.
        self.assertEqual(office_minutes(at(2026, 10, 23, 16), at(2026, 10, 26, 9), Cfg, set()), 120)

    def test_public_holiday_skipped(self):
        holidays = {datetime.date(2026, 10, 23)}
        # 16:00 Thu -> 09:00 Mon with Friday a holiday = 60 + 60.
        self.assertEqual(office_minutes(at(2026, 10, 22, 16), at(2026, 10, 26, 9), Cfg, holidays), 120)

    def test_before_and_after_hours_and_backwards(self):
        self.assertEqual(office_minutes(at(2026, 10, 22, 6), at(2026, 10, 22, 7), Cfg, set()), 0)
        self.assertEqual(office_minutes(at(2026, 10, 22, 10), at(2026, 10, 22, 9), Cfg, set()), 0)


class Base(TestCase):
    def setUp(self):
        self.api = APIClient()
        self.acme = Partner.objects.create(name="Acme")
        self.rival = Partner.objects.create(name="Rival")
        self.tariff = Tariff.objects.create(name="SW 10", price=Decimal("400.00"))
        self.c1 = Customer.objects.create(full_name="Ann Able", email="a@x.com", partner=self.acme, status="online")
        self.c2 = Customer.objects.create(full_name="Ben Rival", email="b@x.com", partner=self.rival, status="online")
        self.admin = User.objects.create_user(username="boss", password="x", role="admin")
        self.mgmt = User.objects.create_user(username="mia", password="x", role="management", allowed_sections=ALL_SECTIONS)
        self.sup = User.objects.create_user(username="sue", password="x", role="support", first_name="Sue",
                                            allowed_sections=ALL_SECTIONS)
        self.sup2 = User.objects.create_user(username="sam", password="x", role="support", first_name="Sam",
                                             allowed_sections=ALL_SECTIONS)
        self.acme_only = User.objects.create_user(username="acme", password="x", role="support",
                                                  allowed_sections=ALL_SECTIONS, allowed_partners=[self.acme.pk])
        self.portal = User.objects.create_user(username="ann", password="x", role="customer")
        Customer.objects.filter(pk=self.c1.pk).update(user=self.portal)

    def ticket(self, customer, created, creator=None, assigned=None, status=Ticket.Status.OPEN, priority="medium",
               resolved=None, reopened=False):
        t = Ticket.objects.create(customer=customer, subject="No internet", created_by=creator, assigned_to=assigned,
                                  status=status, priority=priority)
        Ticket.objects.filter(pk=t.pk).update(created_at=created, resolved_at=resolved, disqualified_from_kpi=reopened)
        t.refresh_from_db()
        return t

    def reply(self, ticket, when, author, internal=False, external=""):
        c = TicketComment.objects.create(ticket=ticket, author=author, message="hi", is_internal=internal,
                                         external_author=external)
        TicketComment.objects.filter(pk=c.pk).update(created_at=when)
        return c


class ResponseStatsTests(Base):
    def setUp(self):
        super().setUp()
        self.cfg = SupportSettings.load()
        self.now = at(2026, 10, 22, 16)  # Thursday 16:00

    def stats(self, user=None, period="week"):
        return response_stats(user or self.admin, period, self.now, self.cfg, set())

    def test_first_reply_definition(self):
        t = self.ticket(self.c1, at(2026, 10, 22, 9), assigned=self.sup)
        self.reply(t, at(2026, 10, 22, 9, 10), self.sup, internal=True)          # internal note: not a reply
        self.reply(t, at(2026, 10, 22, 9, 20), None, external="Ann (Conecto app)")  # the client
        self.reply(t, at(2026, 10, 22, 9, 40), self.sup)                          # first reply
        s = self.stats()
        self.assertEqual((s["client_raised"], s["replied"], s["first_reply_median"]), (1, 1, 40))
        self.assertEqual((s["within"], s["decided"], s["within_pct"]), (1, 1, 100))

    def test_staff_logged_tickets_not_in_first_reply(self):
        self.ticket(self.c1, at(2026, 10, 22, 9), creator=self.sup)  # phoned in
        portal = self.ticket(self.c1, at(2026, 10, 22, 9), creator=self.portal)
        self.reply(portal, at(2026, 10, 22, 11), self.sup)  # 120 min: missed
        s = self.stats()
        self.assertEqual((s["tickets"], s["client_raised"], s["logged_by_staff"]), (2, 1, 1))
        self.assertEqual((s["within"], s["decided"], s["within_pct"]), (0, 1, 0))

    def test_unanswered_counts_as_missed_only_after_target(self):
        self.ticket(self.c1, at(2026, 10, 22, 15, 30))  # 30 office min so far: undecided
        self.ticket(self.c1, at(2026, 10, 22, 13))      # 180 office min: missed
        s = self.stats()
        self.assertEqual((s["decided"], s["within"]), (1, 0))
        self.assertEqual(s["oldest_waiting"]["office_minutes"], 180)

    def test_resolve_time_and_reopened(self):
        self.ticket(self.c1, at(2026, 10, 21, 16), status=Ticket.Status.RESOLVED, resolved=at(2026, 10, 22, 9),
                    creator=self.sup, assigned=self.sup)
        self.ticket(self.c1, at(2026, 10, 22, 9), status=Ticket.Status.RESOLVED, resolved=at(2026, 10, 22, 12),
                    reopened=True, assigned=self.sup)
        s = self.stats()
        # 16:00 Wed -> 09:00 Thu = 60 + 60 = 120; 09:00 -> 12:00 = 180. Median 150.
        self.assertEqual((s["resolved"], s["resolve_median"], s["reopened"]), (2, 150, 1))

    def test_staff_rows_admin_sees_all_support_sees_own(self):
        a = self.ticket(self.c1, at(2026, 10, 22, 9), assigned=self.sup)
        self.reply(a, at(2026, 10, 22, 9, 30), self.sup)
        b = self.ticket(self.c1, at(2026, 10, 22, 9), assigned=self.sup2)
        self.reply(b, at(2026, 10, 22, 12), self.sup2)
        self.ticket(self.c1, at(2026, 10, 22, 10))  # unassigned, missed
        rows = {r["name"]: r for r in self.stats(self.admin)["staff"]}
        self.assertEqual(set(rows), {"Sue", "Sam", "Unassigned"})
        self.assertEqual((rows["Sue"]["within_pct"], rows["Sam"]["within_pct"]), (100, 0))
        own = self.stats(self.sup)
        self.assertEqual([r["name"] for r in own["staff"]], ["Sue"])
        self.assertEqual(own["staff_scope"], "own")
        self.assertEqual(own["decided"], 3)  # team totals are still the whole team

    def test_chart_per_day(self):
        t = self.ticket(self.c1, at(2026, 10, 21, 9))
        self.reply(t, at(2026, 10, 21, 9, 30), self.sup)
        chart = self.stats()["chart"]
        self.assertEqual(len(chart), 7)
        wed = [b for b in chart if b["label"].startswith("Wed")][0]
        self.assertEqual((wed["within"], wed["decided"], wed["pct"]), (1, 1, 100))
        self.assertEqual(len(self.stats(period="month")["chart"]), 5)


class OutageTests(TestCase):
    def row(self, username, customer, minute):
        return {"username": username, "customer": customer, "last_seen": at(2026, 10, 22, 8, minute)}

    def test_threshold_and_window(self):
        points = {f"u{i}": (("ap", 1, ""), "Tower A · Sector 1", "access point") for i in range(6)}
        points["v"] = (("ap", 2, ""), "Tower B", "access point")
        rows = [self.row(f"u{i}", i, m) for i, m in enumerate([10, 11, 12, 13, 14, 50])] + [self.row("v", 99, 10)]
        out = find_outages(rows, points, min_clients=5, window_minutes=15)
        self.assertEqual(len(out), 1)
        self.assertEqual((out[0]["label"], out[0]["count"]), ("Tower A · Sector 1", 5))
        self.assertEqual(find_outages(rows, points, min_clients=6, window_minutes=15), [])
        self.assertEqual(find_outages(rows, points, min_clients=5, window_minutes=2), [])


class EndpointTests(Base):
    def get(self, user, period="week"):
        self.api.force_authenticate(user)
        return self.api.get(f"/api/support/dashboard/?period={period}")

    def test_permissions(self):
        self.assertEqual(self.get(self.sup).status_code, 200)
        no_tickets = User.objects.create_user(username="tech", password="x", role="technician",
                                              allowed_sections=["scheduling"])
        self.assertEqual(self.get(no_tickets).status_code, 403)
        self.assertIn(self.get(self.portal).status_code, (403, 404))
        self.assertEqual(self.get(self.sup, period="year").status_code, 400)

    def test_partner_restriction_and_tiles(self):
        mine = self.ticket(self.c1, timezone.now() - datetime.timedelta(hours=13), priority="urgent")
        self.ticket(self.c2, timezone.now() - datetime.timedelta(hours=1))
        Service.objects.create(customer=self.c1, tariff=self.tariff, status=Service.Status.PENDING,
                               start_date=datetime.date(2026, 10, 1))
        Service.objects.create(customer=self.c2, tariff=self.tariff, status=Service.Status.PENDING,
                               start_date=datetime.date(2026, 10, 1))
        SuspensionExtensionRequest.objects.create(customer=self.c2, extend_until=datetime.date(2026, 12, 1), reason="x")
        data = self.get(self.acme_only).data
        self.assertEqual([r["id"] for r in data["queue"]["results"]], [mine.pk])
        self.assertTrue(data["queue"]["results"][0]["waiting_long"])
        self.assertEqual(data["tiles"]["open_tickets"], {"count": 1, "waiting_long": 1})
        self.assertEqual(data["tiles"]["pending"], 1)
        self.assertEqual(data["tiles"]["extensions"], 0)
        full = self.get(self.admin).data
        self.assertEqual(full["tiles"]["open_tickets"]["count"], 2)
        self.assertEqual((full["tiles"]["pending"], full["tiles"]["extensions"]), (2, 1))
        self.assertEqual(full["queue"]["counts"]["urgent"], 1)

    def test_queue_waiting_states(self):
        answered = self.ticket(self.c1, timezone.now() - datetime.timedelta(hours=3))
        self.reply(answered, timezone.now() - datetime.timedelta(hours=2), self.sup)
        pending = self.ticket(self.c1, timezone.now() - datetime.timedelta(hours=20), status=Ticket.Status.PENDING)
        waiting = self.ticket(self.c1, timezone.now() - datetime.timedelta(hours=5))
        self.reply(waiting, timezone.now() - datetime.timedelta(hours=4), self.sup)
        self.reply(waiting, timezone.now() - datetime.timedelta(hours=1), None, external="Ann")  # client again
        states = {r["id"]: r["waiting_state"] for r in self.get(self.admin).data["queue"]["results"]}
        self.assertEqual(states, {answered.pk: "answered", pending.pk: "on_client", waiting.pk: "waiting"})

    def test_panels_follow_sections(self):
        only = User.objects.create_user(username="desk", password="x", role="support", allowed_sections=["tickets"])
        data = self.get(only).data
        self.assertIsNone(data["offline"])
        self.assertIsNone(data["cutoffs"])
        self.assertIsNone(data["visits"])
        self.assertIsNone(data["tiles"]["pending"])
        self.assertEqual([w["key"] for w in data["waiting"]], ["return_visits"])

    def test_outage_banner_from_access_point(self):
        ap = Device.objects.create(name="Tower A", ip_address="10.1.1.1", device_type="access_point")
        now = timezone.now()
        for i in range(5):
            c = Customer.objects.create(full_name=f"Client {i}", email=f"c{i}@x.com", status="online")
            Service.objects.create(customer=c, tariff=self.tariff, status=Service.Status.ACTIVE,
                                   start_date=datetime.date(2026, 1, 1), radius_username=f"krd_{i}",
                                   access_device=ap, access_detail="Sector B")
            RadAcct.objects.create(
                acctsessionid=f"s{i}", acctuniqueid=f"s{i}", username=f"krd_{i}", nasipaddress="10.0.0.1",
                acctstarttime=now - datetime.timedelta(hours=5), acctstoptime=now - datetime.timedelta(minutes=40 + i),
                acctupdatetime=now - datetime.timedelta(minutes=40 + i), acctterminatecause="Lost-Carrier",
            )
        data = self.get(self.admin).data
        self.assertEqual(data["offline"]["count"], 5)
        [outage] = data["offline"]["outages"]
        self.assertEqual((outage["label"], outage["count"], outage["source"]), ("Tower A · Sector B", 5, "access point"))
        self.assertEqual(data["tiles"]["offline"]["in_outage"], 5)
        self.assertEqual(data["offline"]["results"][0]["network_point"], "Tower A · Sector B")

    def test_settings_admin_only(self):
        self.api.force_authenticate(self.sup)
        self.assertEqual(self.api.get("/api/support/settings/").data["reply_target_minutes"], 60)
        self.assertEqual(self.api.patch("/api/support/settings/", {"reply_target_minutes": 30}, format="json").status_code, 403)
        self.api.force_authenticate(self.admin)
        res = self.api.patch("/api/support/settings/", {"reply_target_minutes": 30, "office_days": [4, 0, 0]}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual((res.data["reply_target_minutes"], res.data["office_days"]), (30, [0, 4]))
        bad = self.api.patch("/api/support/settings/", {"office_start": "18:00", "office_end": "08:00"}, format="json")
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(self.api.patch("/api/support/settings/", {"outage_min_clients": 1}, format="json").status_code, 400)
