"""Active -> Online when a client's line first connects (6 Oct 2026)."""
import datetime
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from billing.models import Service, Tariff
from customers.go_live import promote_live_customers
from customers.models import Customer
from radiusauth.models import RadAcct


class GoLiveTests(TestCase):
    def setUp(self):
        self.tariff = Tariff.objects.create(name="T", price=Decimal("1"), speed_download_kbps=1, speed_upload_kbps=1)

    def _client(self, name, status, username):
        c = Customer.objects.create(full_name=name, status=status)
        Service.objects.create(customer=c, tariff=self.tariff, status="active", start_date="2026-08-01",
                               radius_username=username)
        return c

    def _session(self, username, minutes_ago=1, open_=True):
        when = timezone.now() - datetime.timedelta(minutes=minutes_ago)
        RadAcct.objects.create(
            acctuniqueid=f"u-{username}-{minutes_ago}", acctsessionid="s", username=username, nasipaddress="10.0.0.1",
            acctstarttime=when, acctupdatetime=when, acctstoptime=None if open_ else when,
        )

    def test_active_client_with_live_line_goes_online(self):
        c = self._client("Live", "active", "live1")
        self._session("live1")
        self.assertEqual(promote_live_customers(), 1)
        c.refresh_from_db()
        self.assertEqual(c.status, "online")

    def test_other_statuses_and_dead_sessions_left_alone(self):
        new = self._client("New", "new", "n1")
        sus = self._client("Sus", "suspended", "s1")
        stale = self._client("Stale", "active", "st1")
        closed = self._client("Closed", "active", "c1")
        self._session("n1")
        self._session("s1")
        self._session("st1", minutes_ago=60)
        self._session("c1", open_=False)
        self.assertEqual(promote_live_customers(), 0)
        for c, status in ((new, "new"), (sus, "suspended"), (stale, "active"), (closed, "active")):
            c.refresh_from_db()
            self.assertEqual(c.status, status)

    def test_runs_from_the_minute_job(self):
        c = self._client("Live", "active", "live2")
        self._session("live2")
        out = StringIO()
        call_command("sample_session_usage", stdout=out)
        c.refresh_from_db()
        self.assertEqual(c.status, "online")
        self.assertIn("went live", out.getvalue())
