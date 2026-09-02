"""Byte attribution across hours, days and months.

The whole point of UsageBucket is that a long session's traffic lands on the
days it actually happened, rather than all on the day it connected. These
assert that, plus the edges that quietly corrupt usage figures: counter
resets, double-run samplers, timezone boundaries, and quiet intervals that
must appear as zeros rather than vanish.
"""
import datetime

from django.test import TestCase
from django.utils import timezone

from radiusauth import usage
from radiusauth.models import RadAcct, SessionUsageSnapshot, UsageBucket

MB = 1024 * 1024


def at(y, m, d, h=0, minute=0):
    return timezone.make_aware(
        datetime.datetime(y, m, d, h, minute), timezone.get_current_timezone()
    )


class BankUsageTests(TestCase):
    def test_bytes_land_in_the_hour_they_were_observed(self):
        usage.bank_usage("u1", at(2026, 8, 26, 14, 37), download=5 * MB, upload=1 * MB)
        bucket = UsageBucket.objects.get()
        self.assertEqual(timezone.localtime(bucket.bucket_start).hour, 14)
        self.assertEqual(timezone.localtime(bucket.bucket_start).minute, 0)
        self.assertEqual(bucket.download_bytes, 5 * MB)

    def test_repeated_banking_in_one_hour_adds_up(self):
        for _ in range(3):
            usage.bank_usage("u1", at(2026, 8, 26, 14, 10), download=MB, upload=0)
        self.assertEqual(UsageBucket.objects.get().download_bytes, 3 * MB)
        self.assertEqual(UsageBucket.objects.count(), 1, "one row per username per hour")

    def test_negative_deltas_are_refused(self):
        """A NAS restarting its counters must never subtract real traffic."""
        usage.bank_usage("u1", at(2026, 8, 26, 14), download=5 * MB, upload=0)
        usage.bank_usage("u1", at(2026, 8, 26, 14), download=-99 * MB, upload=0)
        self.assertEqual(UsageBucket.objects.get().download_bytes, 5 * MB)

    def test_nothing_is_written_for_an_idle_interval(self):
        usage.bank_usage("u1", at(2026, 8, 26, 14), download=0, upload=0)
        self.assertEqual(UsageBucket.objects.count(), 0)


class SeriesTests(TestCase):
    def setUp(self):
        # Three hours on the 26th, one on the 24th, one in a different month.
        for hour, mb in ((9, 10), (10, 30), (23, 5)):
            usage.bank_usage("u1", at(2026, 8, 26, hour), download=mb * MB, upload=MB)
        usage.bank_usage("u1", at(2026, 8, 24, 12), download=100 * MB, upload=2 * MB)
        usage.bank_usage("u1", at(2026, 7, 15, 12), download=999 * MB, upload=3 * MB)

    def test_day_gives_24_hourly_points(self):
        s = usage.usage_series(["u1"], "day", datetime.date(2026, 8, 26))
        self.assertEqual(len(s["points"]), 24)
        self.assertEqual(s["interval"], "hour")
        self.assertEqual(s["download_bytes"], 45 * MB)
        by_label = {p["label"]: p["download_bytes"] for p in s["points"]}
        self.assertEqual(by_label["09:00"], 10 * MB)
        self.assertEqual(by_label["10:00"], 30 * MB)
        self.assertEqual(by_label["23:00"], 5 * MB)
        self.assertEqual(by_label["00:00"], 0, "quiet hours appear as zeros, not gaps")

    def test_week_runs_monday_to_sunday(self):
        # 26 Aug 2026 is a Wednesday; that week is Mon 24 -> Sun 30.
        s = usage.usage_series(["u1"], "week", datetime.date(2026, 8, 26))
        self.assertEqual(len(s["points"]), 7)
        self.assertEqual(s["points"][0]["label"], "24 Aug")
        self.assertEqual(s["points"][0]["download_bytes"], 100 * MB)
        self.assertEqual(s["points"][2]["download_bytes"], 45 * MB)
        self.assertEqual(s["download_bytes"], 145 * MB, "July's traffic is not in this week")

    def test_month_is_daily_and_excludes_other_months(self):
        s = usage.usage_series(["u1"], "month", datetime.date(2026, 8, 26))
        self.assertEqual(len(s["points"]), 31)
        self.assertEqual(s["download_bytes"], 145 * MB)

    def test_year_is_monthly(self):
        s = usage.usage_series(["u1"], "year", datetime.date(2026, 8, 26))
        self.assertEqual(len(s["points"]), 12)
        self.assertEqual([p["label"] for p in s["points"]][:3], ["Jan", "Feb", "Mar"])
        by_label = {p["label"]: p["download_bytes"] for p in s["points"]}
        self.assertEqual(by_label["Jul"], 999 * MB)
        self.assertEqual(by_label["Aug"], 145 * MB)
        self.assertEqual(s["download_bytes"], 1144 * MB)

    def test_two_services_are_summed_onto_one_point(self):
        usage.bank_usage("u2", at(2026, 8, 26, 9), download=7 * MB, upload=0)
        s = usage.usage_series(["u1", "u2"], "day", datetime.date(2026, 8, 26))
        by_label = {p["label"]: p["download_bytes"] for p in s["points"]}
        self.assertEqual(by_label["09:00"], 17 * MB)

    def test_a_customer_with_no_logins_gets_a_zeroed_series(self):
        s = usage.usage_series([], "month", datetime.date(2026, 8, 26))
        self.assertEqual(s["total_bytes"], 0)
        self.assertEqual(len(s["points"]), 31)

    def test_unknown_period_is_rejected(self):
        with self.assertRaises(ValueError):
            usage.usage_series(["u1"], "fortnight")


class SamplerBankingTests(TestCase):
    """The sampler is where deltas actually come from, so it is tested against
    real RadAcct rows rather than by calling bank_usage directly."""

    def _session(self, uid, username, in_octets, out_octets):
        RadAcct.objects.update_or_create(
            acctuniqueid=uid,
            defaults=dict(
                acctsessionid=uid, username=username, nasipaddress="10.0.0.1",
                acctstarttime=timezone.now(), acctupdatetime=timezone.now(),
                acctinputoctets=in_octets, acctoutputoctets=out_octets,
            ),
        )

    def _run(self):
        from django.core.management import call_command
        from io import StringIO
        out = StringIO()
        call_command("sample_session_usage", stdout=out)
        return out.getvalue()

    def test_first_sighting_banks_nothing(self):
        """An already-running session's history must not become a spike in the
        hour this command first saw it."""
        self._session("s1", "u1", in_octets=500 * MB, out_octets=900 * MB)
        self._run()
        self.assertEqual(UsageBucket.objects.count(), 0)
        self.assertEqual(SessionUsageSnapshot.objects.count(), 1)

    def test_the_delta_is_banked_not_the_counter(self):
        self._session("s1", "u1", in_octets=500 * MB, out_octets=900 * MB)
        self._run()
        self._session("s1", "u1", in_octets=502 * MB, out_octets=910 * MB)
        self._run()
        bucket = UsageBucket.objects.get()
        self.assertEqual(bucket.download_bytes, 10 * MB, "download is the NAS's output")
        self.assertEqual(bucket.upload_bytes, 2 * MB, "upload is the NAS's input")

    def test_an_idle_interval_banks_nothing_further(self):
        self._session("s1", "u1", in_octets=500 * MB, out_octets=900 * MB)
        self._run()
        self._session("s1", "u1", in_octets=502 * MB, out_octets=910 * MB)
        self._run()
        self._run()
        self._run()
        self.assertEqual(UsageBucket.objects.get().download_bytes, 10 * MB)

    def test_a_counter_reset_banks_the_post_reset_value(self):
        self._session("s1", "u1", in_octets=500 * MB, out_octets=900 * MB)
        self._run()
        self._session("s1", "u1", in_octets=502 * MB, out_octets=910 * MB)
        self._run()
        # NAS restarts its counters mid-session.
        self._session("s1", "u1", in_octets=1 * MB, out_octets=3 * MB)
        self._run()
        bucket = UsageBucket.objects.get()
        self.assertEqual(bucket.download_bytes, 13 * MB, "10 before the reset + 3 after")
        self.assertEqual(bucket.upload_bytes, 3 * MB)

    def test_usage_matches_what_the_rate_was_derived_from(self):
        """Rate and usage come from one measurement, so they cannot disagree."""
        self._session("s1", "u1", in_octets=0, out_octets=0)
        self._run()
        self._session("s1", "u1", in_octets=MB, out_octets=8 * MB)
        self._run()
        snap = SessionUsageSnapshot.objects.get()
        bucket = UsageBucket.objects.get()
        self.assertEqual(bucket.download_bytes, snap.last_output_octets)
        self.assertEqual(bucket.upload_bytes, snap.last_input_octets)
