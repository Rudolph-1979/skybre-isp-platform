"""Sessions on our NASes that this platform does not manage.

The bug: Live Sessions read every row in `radacct` and called it a
customer list. radacct is not a customer list -- it is whatever the NASes
sent us. On 2026-09-15 that was 48 of 55 open sessions belonging to
Grunder Co customers who live on SPLYNX: same company, same routers,
different platform. Their CPE attaches to the PPPoE server whose
service-name is "Skybre", so their Called-Station-Id matches our /radius
entry, which is first in the router's list, so RouterOS hands us their
accounting -- even though we reject every one of their logins.

Two things are covered here: the page shows the lines this platform
manages by default and can still be pointed at the Splynx ones
deliberately, and the prune command never mistakes one of ours for one of
theirs.
"""
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from billing.models import Service, Tariff
from customers.models import Customer
from radiusauth.models import RadAcct, RadCheck


def make_session(username, minutes_ago=1, stopped=False):
    now = timezone.now()
    start = now - timezone.timedelta(minutes=minutes_ago)
    return RadAcct.objects.create(
        acctsessionid=f"s-{username}-{minutes_ago}",
        acctuniqueid=f"u-{username}-{minutes_ago}"[:32],
        username=username,
        nasipaddress="10.200.0.3",
        calledstationid="Skybre",
        acctstarttime=start,
        acctupdatetime=now if not stopped else start,
        acctstoptime=start if stopped else None,
        acctinputoctets=1000,
        acctoutputoctets=2000,
    )


class ForeignSessionFilterTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.staff = User.objects.create_user(username="netops", password="x", role=User.Role.ADMIN)
        self.client.force_authenticate(self.staff)

        tariff = Tariff.objects.create(
            name="20Mbps", price=Decimal("379"), speed_download_kbps=20480, speed_upload_kbps=20480
        )
        customer = Customer.objects.create(full_name="Real Customer", email="real@x.com")
        Service.objects.create(
            customer=customer, tariff=tariff, status=Service.Status.ACTIVE,
            radius_username="skb_real_customer",
        )

        make_session("skb_real_customer")
        make_session("krd_george_morobe")
        make_session("hnm_ben_jacobs")

    def test_defaults_to_our_customers_only(self):
        res = self.client.get("/api/radius-sessions/?state=online")
        self.assertEqual(res.status_code, 200, res.content)
        names = [r["username"] for r in res.json()["results"]]
        self.assertEqual(names, ["skb_real_customer"])

    def test_foreign_shows_only_the_others(self):
        res = self.client.get("/api/radius-sessions/?state=online&owner=foreign")
        names = sorted(r["username"] for r in res.json()["results"])
        self.assertEqual(names, ["hnm_ben_jacobs", "krd_george_morobe"])

    def test_all_shows_everything(self):
        res = self.client.get("/api/radius-sessions/?state=online&owner=all")
        self.assertEqual(len(res.json()["results"]), 3)

    def test_a_service_with_radius_login_disabled_is_still_ours(self):
        """radius_username blank means RADIUS login is off for that service,
        and radcheck gets torn down with it. Its past sessions must not
        become 'another ISP's' and get pruned."""
        RadCheck.objects.create(
            username="skb_switched_off", attribute="Cleartext-Password", op=":=", value="x"
        )
        make_session("skb_switched_off")

        res = self.client.get("/api/radius-sessions/?state=online&owner=foreign")
        names = [r["username"] for r in res.json()["results"]]
        self.assertNotIn("skb_switched_off", names)


class PruneForeignRadacctTests(TestCase):
    def setUp(self):
        tariff = Tariff.objects.create(
            name="20Mbps", price=Decimal("379"), speed_download_kbps=20480, speed_upload_kbps=20480
        )
        customer = Customer.objects.create(full_name="Real Customer", email="real@x.com")
        Service.objects.create(
            customer=customer, tariff=tariff, status=Service.Status.ACTIVE,
            radius_username="skb_real_customer",
        )

    def _run(self, *args):
        out = StringIO()
        call_command("prune_foreign_radacct", *args, stdout=out, stderr=out)
        return out.getvalue()

    def test_dry_run_deletes_nothing(self):
        make_session("krd_george_morobe", stopped=True)
        output = self._run("--no-backup")
        self.assertIn("Dry run", output)
        self.assertEqual(RadAcct.objects.count(), 1)

    def test_apply_deletes_only_foreign_closed_sessions(self):
        make_session("skb_real_customer", stopped=True)
        make_session("krd_george_morobe", stopped=True)
        make_session("hnm_ben_jacobs", stopped=True)

        self._run("--apply", "--no-backup")

        remaining = list(RadAcct.objects.values_list("username", flat=True))
        self.assertEqual(remaining, ["skb_real_customer"])

    def test_open_sessions_are_left_alone_by_default(self):
        """An open foreign session is still receiving interim updates, so
        deleting it just means the next update puts it straight back."""
        make_session("krd_george_morobe", stopped=False)
        self._run("--apply", "--no-backup")
        self.assertEqual(RadAcct.objects.count(), 1)

    def test_include_open_deletes_them(self):
        make_session("krd_george_morobe", stopped=False)
        self._run("--apply", "--include-open", "--no-backup")
        self.assertEqual(RadAcct.objects.count(), 0)

    def test_older_than_days_spares_recent_sessions(self):
        make_session("krd_george_morobe", minutes_ago=5, stopped=True)
        make_session("krd_old_session", minutes_ago=60 * 24 * 30, stopped=True)

        self._run("--apply", "--older-than-days", "7", "--no-backup")

        remaining = list(RadAcct.objects.values_list("username", flat=True))
        self.assertEqual(remaining, ["krd_george_morobe"])

    def test_radcheck_usernames_are_never_pruned(self):
        RadCheck.objects.create(
            username="skb_switched_off", attribute="Cleartext-Password", op=":=", value="x"
        )
        make_session("skb_switched_off", stopped=True)

        self._run("--apply", "--no-backup")

        self.assertTrue(RadAcct.objects.filter(username="skb_switched_off").exists())

    def test_refuses_to_run_when_no_usernames_are_known(self):
        """An empty 'ours' set means the services table didn't load, not
        that we have no customers -- and the difference is the whole
        accounting table."""
        Service.objects.all().delete()
        make_session("krd_george_morobe", stopped=True)

        output = self._run("--apply", "--no-backup")

        self.assertIn("Refusing to run", output)
        self.assertEqual(RadAcct.objects.count(), 1)
