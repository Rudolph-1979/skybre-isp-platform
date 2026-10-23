"""Last session up / down per line (Rudolph, 15 Oct 2026)."""
import datetime

from django.test import TestCase
from django.utils import timezone

from radiusauth.models import RadAcct
from radiusauth.usage import last_sessions


def ago(minutes):
    return timezone.now() - datetime.timedelta(minutes=minutes)


def session(uid, start, stop=None, update=None, cause=None, user="line1"):
    return RadAcct.objects.create(
        acctsessionid=uid, acctuniqueid=uid, username=user, nasipaddress="10.0.0.1",
        acctstarttime=start, acctstoptime=stop, acctupdatetime=update,
        acctterminatecause=cause, acctinputoctets=0, acctoutputoctets=0,
    )


class LastSessionTests(TestCase):
    def test_up_now_with_the_previous_drop(self):
        session("a", ago(3000), stop=ago(200), cause="Lost-Carrier")
        session("b", ago(180), update=ago(2))
        [row] = last_sessions(["line1"])
        self.assertEqual(row["state"], "up")
        self.assertAlmostEqual(row["up_at"].timestamp(), ago(180).timestamp(), delta=5)
        self.assertAlmostEqual(row["down_at"].timestamp(), ago(200).timestamp(), delta=5)
        self.assertEqual(row["down_cause"], "Lost-Carrier")

    def test_down_now(self):
        session("a", ago(300), stop=ago(30), cause="User-Request")
        [row] = last_sessions(["line1"])
        self.assertEqual(row["state"], "down")
        self.assertEqual(row["down_cause"], "User-Request")

    def test_open_but_silent_session(self):
        """No Stop ever arrived (a CPE that lost power): not called 'up'."""
        session("a", ago(600), update=ago(60))
        [row] = last_sessions(["line1"])
        self.assertEqual(row["state"], "silent")
        self.assertIsNone(row["down_at"])
        self.assertAlmostEqual(row["heard_at"].timestamp(), ago(60).timestamp(), delta=5)

    def test_never_connected(self):
        self.assertEqual(last_sessions(["nobody"])[0]["state"], None)
