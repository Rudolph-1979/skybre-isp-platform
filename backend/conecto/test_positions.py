"""Periodic GPS pings: the receiver, and the map endpoint that reads them.

Conecto added periodic position reporting on 2026-09-21, having told us
the day before that no such feed existed. The screen built on the old
answer said, permanently and on the page, that a technician driving
between jobs would not move on it. That sentence is now wrong, and the
thing these tests exist to protect is the sentence that replaced it.

THE ONE FAILURE THAT MATTERS.

A live ping and a three-hour-old check-in draw an identical dot. The
person reading it is deciding whether to send a second technician to a
callout. So every test here is ultimately one assertion in different
shapes: the endpoint never lets a stale reading present itself as a
current one.

Nothing here touches the network.
"""
import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from customers.models import Customer
from scheduling.models import Job

from .models import ConectoJob, ConectoJobEvent, ConectoTechnician, ConectoTechnicianPosition

User = get_user_model()

TOKEN = "test-shared-secret"

CONFIGURED = override_settings(CONECTO_API_KEY=TOKEN, CONECTO_WEBHOOK_TOKEN=TOKEN)

PATH = "/api/webhooks/conecto/technician-position-updated/"

# Two points a few hundred metres apart on the R34 outside Kroonstad.
HERE = (-27.6547, 27.2349)
THERE = (-27.6501, 27.2402)


@CONFIGURED
class PositionReceiverTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.customer = Customer.objects.create(
            full_name="Lucille Slabbert", customer_id="CUS-009100", city="Kroonstad",
        )
        start = timezone.now().replace(microsecond=0)
        self.job = Job.objects.create(
            customer=self.customer, title="Fibre repair", start=start,
            end=start + datetime.timedelta(hours=1),
        )

    def _post(self, payload, token=TOKEN):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token is not None else {}
        return self.client.post(PATH, payload, format="json", **headers)

    def _body(self, **overrides):
        body = {
            "technician_code": "STF-000015",
            "staff_code": None,
            "job_id": str(self.job.pk),
            "lat": HERE[0],
            "lng": HERE[1],
            "recorded_at": timezone.now().isoformat(),
        }
        body.update(overrides)
        return body

    # --- authentication --------------------------------------------------

    def test_an_unauthenticated_ping_is_refused_and_stores_nothing(self):
        self.assertEqual(self._post(self._body(), token=None).status_code, 401)
        self.assertEqual(self._post(self._body(), token="wrong").status_code, 401)
        self.assertEqual(ConectoTechnicianPosition.objects.count(), 0)

    # --- what it accepts -------------------------------------------------

    def test_a_ping_is_stored_against_the_job(self):
        res = self._post(self._body())
        self.assertEqual(res.status_code, 201)

        position = ConectoTechnicianPosition.objects.get()
        self.assertEqual(position.technician_code, "STF-000015")
        self.assertEqual(position.job_id, self.job.pk)
        self.assertEqual(float(position.lat), HERE[0])

    def test_a_staff_code_is_accepted_when_the_technician_code_is_null(self):
        """Conecto sends whichever of the two that phone registered under,
        and said so explicitly. Reading only one field would drop every
        ping from anybody who registered with a staff code -- which, since
        we moved dispatch onto staff codes, is everybody."""
        res = self._post(self._body(technician_code=None, staff_code="STF-000021"))

        self.assertEqual(res.status_code, 201)
        self.assertEqual(ConectoTechnicianPosition.objects.get().technician_code, "STF-000021")

    def test_an_unknown_job_still_stores_the_position(self):
        """The position is true whether or not we can match the job to a
        row. Refusing would throw away a real reading over a bookkeeping
        mismatch -- and Conecto logs failures rather than retrying, so it
        would be thrown away permanently."""
        res = self._post(self._body(job_id="999999"))

        self.assertEqual(res.status_code, 201)
        position = ConectoTechnicianPosition.objects.get()
        self.assertIsNone(position.job_id)

    def test_a_numeric_conecto_job_id_is_not_read_as_our_primary_key(self):
        """Conecto checked their live code on 2026-09-21: job_id on this
        webhook is THEIR Firestore id, not ours, whatever their written
        spec said. So a value that happens to look numeric must still be
        looked up as a Conecto id first -- reading it as our primary key
        would attach the ping to an unrelated job with a straight face."""
        other = Job.objects.create(
            customer=self.customer, title="Something else", start=self.job.start,
            end=self.job.end,
        )
        ConectoJob.objects.create(
            job=self.job, technician_code="STF-000015", conecto_job_id=str(other.pk),
        )
        res = self._post(self._body(job_id=str(other.pk)))

        self.assertEqual(res.status_code, 201)
        self.assertEqual(ConectoTechnicianPosition.objects.get().job_id, self.job.pk)

    def test_a_conecto_job_id_resolves_to_our_job(self):
        """The ordinary case: a Firestore document id we stored when we
        called scheduleJob."""
        ConectoJob.objects.create(
            job=self.job, technician_code="STF-000015", conecto_job_id="cnj-7",
        )
        res = self._post(self._body(job_id="cnj-7"))

        self.assertEqual(res.status_code, 201)
        self.assertEqual(ConectoTechnicianPosition.objects.get().job_id, self.job.pk)

    # --- what it refuses -------------------------------------------------

    def test_a_ping_with_no_identity_is_refused(self):
        res = self._post(self._body(technician_code=None, staff_code=None))

        self.assertEqual(res.status_code, 400)
        self.assertEqual(ConectoTechnicianPosition.objects.count(), 0)

    def test_a_ping_with_no_coordinates_is_refused(self):
        self.assertEqual(self._post(self._body(lat=None, lng=None)).status_code, 400)
        self.assertEqual(self._post(self._body(lat="not-a-number")).status_code, 400)
        self.assertEqual(ConectoTechnicianPosition.objects.count(), 0)

    def test_a_null_island_fix_is_refused(self):
        """0,0 is what a GPS chip reports when it has NO fix. Stored, it
        would put a technician in the Gulf of Guinea with a confident
        timestamp beside them."""
        res = self._post(self._body(lat=0, lng=0))

        self.assertEqual(res.status_code, 400)
        self.assertEqual(ConectoTechnicianPosition.objects.count(), 0)

    def test_an_out_of_range_coordinate_is_refused(self):
        self.assertEqual(self._post(self._body(lat=91)).status_code, 400)
        self.assertEqual(ConectoTechnicianPosition.objects.count(), 0)

    # --- replays ---------------------------------------------------------

    def test_the_same_ping_twice_is_stored_once(self):
        body = self._body()
        first = self._post(body)
        second = self._post(body)

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()["duplicate"])
        self.assertEqual(ConectoTechnicianPosition.objects.count(), 1)

    def test_a_missing_timestamp_does_not_lose_the_reading(self):
        res = self._post(self._body(recorded_at=None))

        self.assertEqual(res.status_code, 201)
        self.assertIsNotNone(ConectoTechnicianPosition.objects.get().recorded_at)


class PositionsEndpointTests(TestCase):
    """What the map is allowed to claim."""

    def setUp(self):
        self.client = APIClient()
        self.staff = User.objects.create_user(
            username="dispatcher", password="x", role=User.Role.ADMIN, is_staff=True,
        )
        self.tech = User.objects.create_user(
            username="bernard", password="x", first_name="Bernard",
            role=User.Role.TECHNICIAN,
        )
        self.client.force_authenticate(self.staff)

        self.customer = Customer.objects.create(
            full_name="Lucille Slabbert", customer_id="CUS-009101", city="Kroonstad",
        )
        start = timezone.now().replace(microsecond=0)
        self.job = Job.objects.create(
            customer=self.customer, title="Fibre repair", start=start,
            end=start + datetime.timedelta(hours=1), assigned_to=self.tech,
        )

    def _rows(self):
        res = self.client.get("/api/technician-positions/")
        self.assertEqual(res.status_code, 200)
        # {count, results}, like every other list endpoint. Asserted rather
        # than assumed: this shape is what useApiList reads, and when it
        # was a bare array the page rendered a white screen with a green
        # build and a 200 in the network tab.
        payload = res.json()
        self.assertIn("results", payload)
        return {row["code"]: row for row in payload["results"]}

    def _ping(self, minutes_ago, lat, lng):
        return ConectoTechnicianPosition.objects.create(
            technician_code=self.tech.staff_code,
            job=self.job,
            lat=lat, lng=lng,
            recorded_at=timezone.now() - datetime.timedelta(minutes=minutes_ago),
        )

    def _event(self, minutes_ago, status, lat=None, lng=None):
        return ConectoJobEvent.objects.create(
            job=self.job,
            technician_code=self.tech.staff_code,
            status=status,
            occurred_at=timezone.now() - datetime.timedelta(minutes=minutes_ago),
            lat=lat, lng=lng,
        )

    # --- the distinction the screen turns on -----------------------------

    def test_a_recent_ping_is_live(self):
        self._event(10, "en_route")
        self._ping(1, *HERE)

        row = self._rows()[self.tech.staff_code]
        self.assertEqual(row["source"], "ping")
        self.assertTrue(row["live"])
        self.assertEqual(row["status"], "en_route")

    def test_an_old_ping_is_not_live(self):
        """The one thing this endpoint must never do. Nothing about a pin
        from forty minutes ago looks different on a map."""
        self._event(50, "en_route")
        self._ping(40, *HERE)

        row = self._rows()[self.tech.staff_code]
        self.assertEqual(row["source"], "ping")
        self.assertFalse(row["live"])

    def test_a_status_position_is_never_live(self):
        """A check-in is a real position and usually the customer's own
        address. It is not a claim about where somebody is now, however
        recently it happened."""
        self._event(1, "checked_in", lat=THERE[0], lng=THERE[1])

        row = self._rows()[self.tech.staff_code]
        self.assertEqual(row["source"], "status")
        self.assertFalse(row["live"])

    def test_the_newer_reading_wins(self):
        self._event(30, "en_route", lat=THERE[0], lng=THERE[1])
        self._ping(2, *HERE)

        row = self._rows()[self.tech.staff_code]
        self.assertEqual(row["source"], "ping")
        self.assertAlmostEqual(row["lat"], HERE[0], places=5)

    def test_a_check_in_after_the_last_ping_takes_over(self):
        """Pings stop at check-in, so the newest thing on a technician who
        has arrived is the arrival itself -- and the pin should stop
        claiming to be live at that moment, not four minutes later."""
        self._ping(10, *HERE)
        self._event(2, "checked_in", lat=THERE[0], lng=THERE[1])

        row = self._rows()[self.tech.staff_code]
        self.assertEqual(row["source"], "status")
        self.assertFalse(row["live"])
        self.assertAlmostEqual(row["lat"], THERE[0], places=5)

    # --- the trail -------------------------------------------------------

    def test_the_trail_runs_oldest_to_newest(self):
        """Drawn straight into a polyline, so a reversed one would show the
        technician driving backwards."""
        self._ping(6, *HERE)
        self._ping(4, -27.653, 27.237)
        self._ping(2, *THERE)

        trail = self._rows()[self.tech.staff_code]["trail"]
        self.assertEqual(len(trail), 3)
        self.assertAlmostEqual(trail[0][0], HERE[0], places=5)
        self.assertAlmostEqual(trail[-1][0], THERE[0], places=5)

    def test_yesterdays_drive_is_not_on_todays_map(self):
        self._ping(60 * 20, *HERE)

        row = self._rows()[self.tech.staff_code]
        self.assertEqual(row["trail"], [])
        self.assertIsNone(row["lat"])

    # --- who appears -----------------------------------------------------

    def test_a_technician_who_has_reported_nothing_is_still_listed(self):
        """Omitting them would read as "nothing to report" rather than "we
        do not know where this person is"."""
        row = self._rows()[self.tech.staff_code]

        self.assertIsNone(row["lat"])
        self.assertIsNone(row["minutes_ago"])
        self.assertEqual(row["source"], "")
        self.assertFalse(row["live"])

    def test_a_hand_set_technician_code_resolves_to_the_person(self):
        ConectoTechnician.objects.create(user=self.tech, technician_code="TECH-9")
        ConectoTechnicianPosition.objects.create(
            technician_code="TECH-9", lat=HERE[0], lng=HERE[1],
            recorded_at=timezone.now(),
        )

        rows = self._rows()
        self.assertEqual(rows["TECH-9"]["name"], "Bernard")

    def test_live_technicians_sort_first(self):
        other = User.objects.create_user(
            username="aaron", password="x", first_name="Aaron",
            role=User.Role.TECHNICIAN,
        )
        ConectoTechnicianPosition.objects.create(
            technician_code=other.staff_code, lat=THERE[0], lng=THERE[1],
            recorded_at=timezone.now() - datetime.timedelta(hours=3),
        )
        self._ping(1, *HERE)

        res = self.client.get("/api/technician-positions/")
        self.assertEqual(res.json()["results"][0]["name"], "Bernard")

    # --- who may look ----------------------------------------------------

    def test_a_customer_cannot_read_technician_positions(self):
        customer_user = User.objects.create_user(
            username="lucille", password="x", role=User.Role.CUSTOMER,
        )
        self.client.force_authenticate(customer_user)

        self.assertIn(self.client.get("/api/technician-positions/").status_code, (403, 404))
