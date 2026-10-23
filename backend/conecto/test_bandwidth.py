"""Live bandwidth: the three calls Conecto make on us.

The first endpoints in this integration where WE are asked rather than
told, and the first where a customer is looking at the answer in real
time on their own phone.

WHAT THESE TESTS ARE REALLY ABOUT

One number, shown honestly. There are four ways to show a wrong one here
and every test below is one of them:

  * a rate needs two samples, so the first moment of every session has no
    rate -- and 0.0 Mbps on the screen somebody opened to check their line
    reads as "my internet is down";
  * a customer with no session has nothing to measure, which is not the
    same as measuring zero;
  * a router with no API access will never produce a live figure, however
    long the app polls, and saying so beats polling forever;
  * accounting figures are an average over the NAS's interim interval, so
    a speed test reads low on them. They must never be presented as live.

Plus the resource half: a session that ends when it should, whether or not
`stop` ever arrives.

Nothing here touches a router.
"""
import datetime
import uuid
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from customers.models import Customer

from .models import ConectoBandwidthSession

TOKEN = "test-shared-secret"

CONFIGURED = override_settings(CONECTO_API_KEY=TOKEN, CONECTO_WEBHOOK_TOKEN=TOKEN)

START = "/api/conecto/bandwidth/start/"
POLL = "/api/conecto/bandwidth/poll/"
STOP = "/api/conecto/bandwidth/stop/"

USERNAME = "lucille@skybre"


def session_row(rate_source="router", download_bps=48_000_000, upload_bps=12_000_000):
    """One row shaped like radiusauth.usage.live_state returns."""
    return {
        "username": USERNAME,
        "started_at": timezone.now() - datetime.timedelta(hours=2),
        "ip_address": "10.44.0.7",
        "mac_address": "AA:BB:CC:DD:EE:FF",
        "download_bytes": 1_000_000,
        "upload_bytes": 250_000,
        "download_bps": download_bps,
        "upload_bps": upload_bps,
        "rate_measured_at": timezone.now(),
        "rate_source": rate_source,
    }


@CONFIGURED
class BandwidthSessionTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        # live_bandwidth_public deliberately LEFT OFF -- its default. Every
        # test in this class therefore runs against a customer who has never
        # been opted in, which is the normal case and the thing that used to
        # fail.
        self.customer = Customer.objects.create(
            full_name="Lucille Slabbert", customer_id="CUS-007700", city="Kroonstad",
        )

    # --- helpers ---------------------------------------------------------

    def _post(self, path, payload, token=TOKEN):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token is not None else {}
        return self.client.post(path, payload, format="json", **headers)

    def _start(self, **patches):
        defaults = {
            "usernames": [USERNAME],
            "sessions": [session_row()],
            "watched": 1,
        }
        defaults.update(patches)
        with patch("radiusauth.usage.usernames_for_customer", return_value=defaults["usernames"]), \
             patch("radiusauth.usage.live_state", return_value=defaults["sessions"]), \
             patch("radiusauth.usage.request_live_readings", return_value=defaults["watched"]):
            return self._post(START, {"client_code": self.customer.customer_id})

    def _poll(self, session_id, **patches):
        defaults = {
            "usernames": [USERNAME],
            "sessions": [session_row()],
            "watched": 1,
        }
        defaults.update(patches)
        with patch("radiusauth.usage.usernames_for_customer", return_value=defaults["usernames"]), \
             patch("radiusauth.usage.live_state", return_value=defaults["sessions"]), \
             patch("radiusauth.usage.request_live_readings", return_value=defaults["watched"]):
            return self._post(POLL, {"session_id": session_id})

    # --- authentication --------------------------------------------------

    def test_an_unauthenticated_start_is_refused(self):
        self.assertEqual(
            self._post(START, {"client_code": self.customer.customer_id}, token=None).status_code,
            401,
        )
        self.assertEqual(
            self._post(START, {"client_code": self.customer.customer_id}, token="wrong").status_code,
            401,
        )
        self.assertEqual(ConectoBandwidthSession.objects.count(), 0)

    def test_an_unknown_client_code_is_refused(self):
        res = self._post(START, {"client_code": "CUS-999999"})

        self.assertEqual(res.status_code, 404)
        self.assertEqual(ConectoBandwidthSession.objects.count(), 0)

    # --- the states ------------------------------------------------------

    def test_a_router_rate_is_live_and_totalled(self):
        res = self._start()

        self.assertEqual(res.status_code, 201)
        body = res.json()
        self.assertEqual(body["state"], "live")
        self.assertEqual(body["download_mbps"], 48.0)
        self.assertEqual(body["upload_mbps"], 12.0)
        self.assertTrue(body["session_id"])

    def test_an_accounting_rate_is_warming_not_live(self):
        """THE MOST IMPORTANT ONE HERE. An accounting figure is an average
        over the NAS's interim interval -- a speed test reads low on it.
        Shown as live it is the map pin that looks current and is an hour
        old, except on a customer's own phone."""
        res = self._start(sessions=[session_row(rate_source="accounting")])

        body = res.json()
        self.assertEqual(body["state"], "warming")
        # And it does NOT quietly report the average as the figure.
        self.assertEqual(body["download_mbps"], 0.0)

    def test_no_rate_at_all_is_warming(self):
        """The first second of every session. Returning 0.0 here reads, on
        the screen somebody opened to check their line, as "it is dead"."""
        res = self._start(sessions=[session_row(rate_source=None)])

        self.assertEqual(res.json()["state"], "warming")

    def test_no_active_session_is_offline(self):
        res = self._start(sessions=[])

        body = res.json()
        self.assertEqual(body["state"], "offline")
        self.assertEqual(body["sessions"], [])

    def test_a_router_without_api_access_is_unavailable(self):
        """Nothing will ever produce a live figure here, so say so rather
        than let the app poll for five minutes."""
        res = self._start(watched=0)

        body = res.json()
        self.assertEqual(body["state"], "unavailable")
        self.assertEqual(body["reason"], "no_api_device")

    def test_no_opt_in_is_needed_for_the_app(self):
        """Opening the screen is the authorisation. The session was created
        by an authenticated call on the client's behalf and dies in five
        minutes; nothing needs enabling first."""
        self.assertFalse(self.customer.live_bandwidth_public)

        res = self._start()

        self.assertEqual(res.json()["state"], "live")

    def test_a_session_never_touches_the_public_usage_link(self):
        """THE REASON THIS IS NOT IMPLEMENTED BY FLIPPING THE FLAG.

        live_bandwidth_public also governs the no-login usage link staff
        send customers. Turning it on for a five-minute app session would
        grant link access nobody asked for; turning it off afterwards would
        revoke it from customers staff had deliberately enabled -- one
        feature silently switching another one off.
        """
        self._start()
        self.customer.refresh_from_db()
        self.assertFalse(self.customer.live_bandwidth_public)

        # And the other direction: a customer who HAS the link keeps it.
        self.customer.live_bandwidth_public = True
        self.customer.save()

        self._start()

        self.customer.refresh_from_db()
        self.assertTrue(self.customer.live_bandwidth_public)

    def test_several_logins_are_summed_and_broken_down(self):
        """Their schema assumes one pair of numbers; several of our
        customers have more than one concurrent session."""
        second = session_row(download_bps=2_000_000, upload_bps=1_000_000)
        second["username"] = "lucille-backup@skybre"
        res = self._start(sessions=[session_row(), second])

        body = res.json()
        self.assertEqual(body["download_mbps"], 50.0)
        self.assertEqual(body["upload_mbps"], 13.0)
        self.assertEqual(len(body["sessions"]), 2)

    # --- the session lifecycle -------------------------------------------

    def test_polling_renews_nothing_but_records_itself(self):
        session_id = self._start().json()["session_id"]

        self._poll(session_id)
        self._poll(session_id)

        row = ConectoBandwidthSession.objects.get(session_id=session_id)
        self.assertEqual(row.poll_count, 2)
        self.assertIsNotNone(row.last_polled_at)

    def test_stop_ends_the_session(self):
        session_id = self._start().json()["session_id"]

        self.assertEqual(self._post(STOP, {"session_id": session_id}).status_code, 200)

        self.assertIsNotNone(
            ConectoBandwidthSession.objects.get(session_id=session_id).stopped_at
        )

    def test_polling_a_stopped_session_says_expired(self):
        session_id = self._start().json()["session_id"]
        self._post(STOP, {"session_id": session_id})

        self.assertEqual(self._poll(session_id).json()["state"], "expired")

    def test_the_cutoff_holds_without_a_stop(self):
        """The whole point of the five minutes. A phone that loses signal
        mid-session never sends stop, and Conecto asked us to enforce this
        regardless -- so it must not depend on their call arriving."""
        session_id = self._start().json()["session_id"]
        ConectoBandwidthSession.objects.filter(session_id=session_id).update(
            expires_at=timezone.now() - datetime.timedelta(seconds=1)
        )

        self.assertEqual(self._poll(session_id).json()["state"], "expired")
        self.assertIsNotNone(
            ConectoBandwidthSession.objects.get(session_id=session_id).stopped_at
        )

    def test_starting_again_replaces_the_previous_session(self):
        """A client who backgrounds the app and reopens it should resume,
        not accumulate sessions nobody will ever stop."""
        first = self._start().json()["session_id"]
        second = self._start().json()["session_id"]

        self.assertNotEqual(first, second)
        self.assertIsNotNone(
            ConectoBandwidthSession.objects.get(session_id=first).stopped_at
        )
        self.assertIsNone(
            ConectoBandwidthSession.objects.get(session_id=second).stopped_at
        )

    # --- bad input -------------------------------------------------------

    def test_an_unknown_session_is_a_404_not_a_crash(self):
        self.assertEqual(self._post(POLL, {"session_id": str(uuid.uuid4())}).status_code, 404)

    def test_a_malformed_session_id_is_a_404_not_a_500(self):
        """Postgres raises on a bad UUID literal, which would turn a client
        mistake into a server error."""
        self.assertEqual(self._post(POLL, {"session_id": "not-a-uuid"}).status_code, 404)
        self.assertEqual(self._post(POLL, {}).status_code, 404)

    def test_stopping_an_unknown_session_is_not_an_error(self):
        """There is nothing useful a client can do about a failed stop, and
        Conecto log and drop failures rather than retrying."""
        self.assertEqual(self._post(STOP, {"session_id": str(uuid.uuid4())}).status_code, 200)
        self.assertEqual(self._post(STOP, {}).status_code, 200)
