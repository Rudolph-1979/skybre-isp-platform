"""Fair use, and time-of-day bursting.

The cases worth testing are the ones where a plausible implementation is
quietly wrong: a window that runs through midnight never firing, a
fair-use rule shaping a line to zero, off-peak traffic still counting
toward the threshold it was meant to relieve, and a scheduled job
re-pushing an identical rate limit to every session twelve times an hour.
"""
import datetime
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from billing.models import Service, Tariff
from customers.models import Customer
from radiusauth.models import SpeedWindow, UsageBucket
from radiusauth.signals import _mikrotik_rate_limit
from radiusauth.speeds import countable_usage_gb, effective_speeds


def local(y, m, d, hh, mm=0):
    return timezone.make_aware(datetime.datetime(y, m, d, hh, mm))


class WindowCoverageTests(TestCase):
    def _window(self, start, end, **kwargs):
        return SpeedWindow.objects.create(
            name="Night burst",
            start_time=datetime.time(*start),
            end_time=datetime.time(*end),
            **kwargs,
        )

    def test_a_normal_window_covers_only_its_own_hours(self):
        window = self._window((13, 0), (17, 0))
        self.assertTrue(window.covers(local(2026, 8, 27, 14)))
        self.assertFalse(window.covers(local(2026, 8, 27, 12)))
        self.assertFalse(window.covers(local(2026, 8, 27, 17)))

    def test_a_window_through_midnight_is_not_the_empty_set(self):
        """The one that a naive start <= t <= end test silently makes
        never fire -- and 22:00-06:00 is the shape almost every off-peak
        window actually has."""
        window = self._window((22, 0), (6, 0))
        self.assertTrue(window.covers(local(2026, 8, 27, 23)))
        self.assertTrue(window.covers(local(2026, 8, 28, 1)))
        self.assertTrue(window.covers(local(2026, 8, 28, 5, 59)))
        self.assertFalse(window.covers(local(2026, 8, 28, 6)))
        self.assertFalse(window.covers(local(2026, 8, 27, 21, 59)))

    def test_a_friday_night_window_still_covers_saturday_small_hours(self):
        """"Friday night" has to mean Friday 22:00 through Saturday 06:00.
        Attributing 01:00 Saturday to Saturday would make the window end
        at midnight, which is not what anybody means by a night."""
        # 2026-08-28 is a Friday; weekday() == 4.
        window = self._window((22, 0), (6, 0), weekdays=[4])
        self.assertTrue(window.covers(local(2026, 8, 28, 23)))
        self.assertTrue(window.covers(local(2026, 8, 29, 2)))
        self.assertFalse(window.covers(local(2026, 8, 29, 23)))

    def test_an_inactive_window_covers_nothing(self):
        window = self._window((22, 0), (6, 0), is_active=False)
        self.assertFalse(window.covers(local(2026, 8, 27, 23)))

    def test_empty_weekdays_means_every_day(self):
        window = self._window((13, 0), (17, 0), weekdays=[])
        for day in range(24, 31):
            self.assertTrue(window.covers(local(2026, 8, day, 14)))


class EffectiveSpeedTests(TestCase):
    def setUp(self):
        self.tariff = Tariff.objects.create(
            name="20 Mbps Uncapped", price=Decimal("899.00"),
            speed_download_kbps=20480, speed_upload_kbps=10240,
        )
        self.customer = Customer.objects.create(full_name="Heavy User", email="h@x.com")
        self.service = Service.objects.create(
            customer=self.customer, tariff=self.tariff, status=Service.Status.ACTIVE,
            start_date="2026-08-01", radius_username="heavy1",
        )

    def _use(self, gb, when):
        UsageBucket.objects.create(
            username="heavy1",
            bucket_start=when.replace(minute=0, second=0, microsecond=0),
            download_bytes=int(gb * 1024 ** 3),
            upload_bytes=0,
        )

    def _window(self, start, end, pct=200, **kwargs):
        return SpeedWindow.objects.create(
            name="Night burst", start_time=datetime.time(*start), end_time=datetime.time(*end),
            speed_pct=pct, **kwargs,
        )

    def test_with_no_policy_at_all_nothing_changes(self):
        """Every existing plan is in this state. Adding the feature must
        not alter a single line until somebody configures something."""
        result = effective_speeds(self.service, local(2026, 8, 27, 14))
        self.assertEqual(result.upload_kbps, 10240)
        self.assertEqual(result.download_kbps, 20480)
        self.assertEqual(result.reason, "plan")

    def test_a_window_doubles_the_line(self):
        self._window((22, 0), (6, 0), pct=200)
        result = effective_speeds(self.service, local(2026, 8, 27, 23))
        self.assertEqual(result.download_kbps, 40960)
        self.assertEqual(result.upload_kbps, 20480)
        self.assertEqual(result.reason, "window")
        self.assertEqual(result.window_name, "Night burst")

    def test_outside_the_window_the_line_is_normal(self):
        self._window((22, 0), (6, 0), pct=200)
        result = effective_speeds(self.service, local(2026, 8, 27, 14))
        self.assertEqual(result.download_kbps, 20480)
        self.assertEqual(result.reason, "plan")

    def test_past_the_threshold_the_line_is_shaped(self):
        self.tariff.fup_threshold_gb = 100
        self.tariff.fup_speed_pct = 30
        self.tariff.save()
        self._use(150, local(2026, 8, 10, 14))
        result = effective_speeds(self.service, local(2026, 8, 27, 14))
        self.assertTrue(result.shaped)
        self.assertEqual(result.reason, "fup")
        self.assertEqual(result.download_kbps, 6144)  # 30% of 20480

    def test_below_the_threshold_it_is_not(self):
        self.tariff.fup_threshold_gb = 100
        self.tariff.save()
        self._use(40, local(2026, 8, 10, 14))
        result = effective_speeds(self.service, local(2026, 8, 27, 14))
        self.assertFalse(result.shaped)
        self.assertEqual(result.download_kbps, 20480)

    def test_the_window_BEATS_the_shaping(self):
        """The decision this design turns on. A shaped customer still gets
        the boost -- the hours are empty anyway, and it turns a 19:00
        phone call into something they can work around at 01:00."""
        self.tariff.fup_threshold_gb = 100
        self.tariff.save()
        self._use(150, local(2026, 8, 10, 14))
        self._window((22, 0), (6, 0), pct=200)

        peak = effective_speeds(self.service, local(2026, 8, 27, 14))
        self.assertEqual(peak.reason, "fup")
        self.assertEqual(peak.download_kbps, 6144)

        night = effective_speeds(self.service, local(2026, 8, 27, 23))
        self.assertEqual(night.reason, "window over fup")
        self.assertEqual(night.download_kbps, 40960)
        # Still reported as shaped, so nobody concludes the fair-use rule
        # has stopped working.
        self.assertTrue(night.shaped)

    def test_shaping_never_reduces_a_line_to_zero(self):
        """0k on a Mikrotik rate limit is not "very slow", it is a line
        that passes nothing. A fair-use rule must not disconnect people."""
        slow = Tariff.objects.create(
            name="512k", price=Decimal("199.00"),
            speed_download_kbps=512, speed_upload_kbps=256,
        )
        slow.fup_threshold_gb = 1
        slow.fup_speed_pct = 1
        slow.save()
        self.service.tariff = slow
        self.service.save()
        self._use(5, local(2026, 8, 10, 14))
        result = effective_speeds(self.service, local(2026, 8, 27, 14))
        self.assertGreater(result.download_kbps, 0)
        self.assertGreater(result.upload_kbps, 0)

    def test_an_exempt_line_is_never_shaped(self):
        self.tariff.fup_threshold_gb = 100
        self.tariff.save()
        self._use(500, local(2026, 8, 10, 14))
        self.service.fup_exempt = True
        self.service.save()
        result = effective_speeds(self.service, local(2026, 8, 27, 14))
        self.assertFalse(result.shaped)
        self.assertEqual(result.download_kbps, 20480)

    def test_a_service_override_beats_the_tariff(self):
        self.tariff.fup_threshold_gb = 100
        self.tariff.save()
        self._use(150, local(2026, 8, 10, 14))
        self.service.fup_threshold_gb = 500
        self.service.save()
        result = effective_speeds(self.service, local(2026, 8, 27, 14))
        self.assertFalse(result.shaped, "the line's own higher threshold should win")

    def test_a_zero_threshold_override_shapes_immediately(self):
        """0 is a real value, distinct from "not set" -- which is why
        these fields are nullable rather than zero-defaulted."""
        self.service.fup_threshold_gb = 0
        self.service.save()
        result = effective_speeds(self.service, local(2026, 8, 27, 14))
        self.assertTrue(result.shaped)

    def test_a_window_scoped_to_one_tariff_leaves_others_alone(self):
        other = Tariff.objects.create(
            name="Business", price=Decimal("2400.00"),
            speed_download_kbps=51200, speed_upload_kbps=51200,
        )
        self._window((22, 0), (6, 0), pct=200, tariff=other)
        result = effective_speeds(self.service, local(2026, 8, 27, 23))
        self.assertEqual(result.reason, "plan")

    def test_the_most_generous_overlapping_window_wins(self):
        self._window((22, 0), (6, 0), pct=150)
        SpeedWindow.objects.create(
            name="Deep night", start_time=datetime.time(0, 0), end_time=datetime.time(4, 0),
            speed_pct=300,
        )
        result = effective_speeds(self.service, local(2026, 8, 28, 2))
        self.assertEqual(result.download_kbps, 20480 * 3)


class OffPeakAccountingTests(TestCase):
    """Off-peak traffic not counting toward fair use is the entire reason
    to run an off-peak window. A window that still counts gives nobody a
    reason to move their downloads into it, and the evening stays exactly
    as congested."""

    def setUp(self):
        self.tariff = Tariff.objects.create(
            name="20 Mbps", price=Decimal("899.00"),
            speed_download_kbps=20480, speed_upload_kbps=10240, fup_threshold_gb=100,
        )
        self.customer = Customer.objects.create(full_name="Night Owl", email="n@x.com")
        self.service = Service.objects.create(
            customer=self.customer, tariff=self.tariff, status=Service.Status.ACTIVE,
            start_date="2026-08-01", radius_username="owl1",
        )

    def _use(self, gb, hour, day=10):
        UsageBucket.objects.create(
            username="owl1",
            bucket_start=local(2026, 8, day, hour),
            download_bytes=int(gb * 1024 ** 3),
            upload_bytes=0,
        )

    def test_off_peak_traffic_is_excluded_by_default(self):
        SpeedWindow.objects.create(
            name="Night burst", start_time=datetime.time(22, 0), end_time=datetime.time(6, 0),
            speed_pct=200,
        )
        self._use(200, hour=2)   # inside the window
        self._use(10, hour=14)   # peak
        counted = countable_usage_gb(self.service, local(2026, 8, 27, 14))
        self.assertAlmostEqual(counted, 10, places=1)
        self.assertFalse(effective_speeds(self.service, local(2026, 8, 27, 14)).shaped)

    def test_a_window_set_to_count_does_count(self):
        SpeedWindow.objects.create(
            name="Counted burst", start_time=datetime.time(22, 0), end_time=datetime.time(6, 0),
            speed_pct=200, counts_toward_fup=True,
        )
        self._use(200, hour=2)
        counted = countable_usage_gb(self.service, local(2026, 8, 27, 14))
        self.assertAlmostEqual(counted, 200, places=1)
        self.assertTrue(effective_speeds(self.service, local(2026, 8, 27, 14)).shaped)

    def test_usage_from_a_previous_month_does_not_count(self):
        self._use(500, hour=14, day=10)
        UsageBucket.objects.create(
            username="owl1", bucket_start=local(2026, 7, 15, 14),
            download_bytes=int(900 * 1024 ** 3), upload_bytes=0,
        )
        counted = countable_usage_gb(self.service, local(2026, 8, 27, 14))
        self.assertAlmostEqual(counted, 500, places=1)


class RateLimitStringTests(TestCase):
    def setUp(self):
        self.tariff = Tariff.objects.create(
            name="10 Mbps", price=Decimal("599.00"),
            speed_download_kbps=10240, speed_upload_kbps=5120,
        )
        self.customer = Customer.objects.create(full_name="Wire Format", email="w@x.com")
        self.service = Service.objects.create(
            customer=self.customer, tariff=self.tariff, status=Service.Status.ACTIVE,
            start_date="2026-08-01", radius_username="wire1",
        )

    def test_the_rate_limit_is_upload_then_download_with_a_k_suffix(self):
        """rx is the rate FROM the client. Getting this backwards is
        invisible until somebody notices their download is capped at
        their upload speed."""
        self.assertEqual(_mikrotik_rate_limit(self.service), "5120k/10240k")

    def test_it_reflects_the_policy_not_just_the_plan(self):
        """The radreply written at login and the CoA sent to a live
        session both come through here, so they can never disagree."""
        SpeedWindow.objects.create(
            name="All day double", start_time=datetime.time(0, 0), end_time=datetime.time(23, 59),
            speed_pct=200,
        )
        self.assertEqual(_mikrotik_rate_limit(self.service), "10240k/20480k")


class ScheduledPushTests(TestCase):
    def setUp(self):
        self.tariff = Tariff.objects.create(
            name="10 Mbps", price=Decimal("599.00"),
            speed_download_kbps=10240, speed_upload_kbps=5120,
        )
        self.customer = Customer.objects.create(full_name="Live One", email="l@x.com")
        self.service = Service.objects.create(
            customer=self.customer, tariff=self.tariff, status=Service.Status.ACTIVE,
            start_date="2026-08-01", radius_username="live1",
        )

    def test_the_dry_run_reports_a_line_whose_rate_has_changed(self):
        from io import StringIO

        from django.core.management import call_command

        SpeedWindow.objects.create(
            name="All day double", start_time=datetime.time(0, 0), end_time=datetime.time(23, 59),
            speed_pct=200,
        )
        Service.objects.filter(pk=self.service.pk).update(last_pushed_rate_limit="5120k/10240k")
        out = StringIO()
        call_command("apply_speed_policies", "--username=live1", stdout=out)
        text = out.getvalue()
        # No live session in this test, so it is counted as not connected
        # rather than pushed -- and that distinction is the point: the new
        # value is already in radreply for their next login.
        self.assertIn("not connected", text)

    def test_an_unchanged_rate_is_skipped_entirely(self):
        """Otherwise a five-minute cron re-sends an identical limit to
        every session twelve times an hour."""
        from io import StringIO

        from django.core.management import call_command

        Service.objects.filter(pk=self.service.pk).update(last_pushed_rate_limit="5120k/10240k")
        out = StringIO()
        call_command("apply_speed_policies", "--username=live1", stdout=out)
        self.assertIn("1 already correct", out.getvalue())


class SpeedNowApiTests(TestCase):
    def setUp(self):
        from rest_framework.test import APIClient

        self.client = APIClient()
        self.staff = User.objects.create_user(username="desk", password="x", role=User.Role.ADMIN)
        self.client.force_authenticate(self.staff)
        self.tariff = Tariff.objects.create(
            name="20 Mbps", price=Decimal("899.00"),
            speed_download_kbps=20480, speed_upload_kbps=10240, fup_threshold_gb=100,
        )
        self.customer = Customer.objects.create(full_name="Asking Why", email="a@x.com")
        self.service = Service.objects.create(
            customer=self.customer, tariff=self.tariff, status=Service.Status.ACTIVE,
            start_date="2026-08-01", radius_username="why1",
        )

    def test_it_explains_a_shaped_line_in_one_sentence(self):
        """Because otherwise "my internet is slow" has no answer anybody
        can give without reading code."""
        UsageBucket.objects.create(
            username="why1", bucket_start=timezone.localtime().replace(day=1, hour=14, minute=0, second=0, microsecond=0),
            download_bytes=int(150 * 1024 ** 3), upload_bytes=0,
        )
        res = self.client.get(f"/api/services/{self.service.pk}/speed-now/")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["shaped"])
        self.assertIn("fair use", res.data["explanation"])
        self.assertEqual(res.data["plan_download_kbps"], 20480)
        self.assertLess(res.data["download_kbps"], 20480)

    def test_a_full_speed_line_says_so(self):
        res = self.client.get(f"/api/services/{self.service.pk}/speed-now/")
        self.assertEqual(res.data["reason"], "plan")
        self.assertIn("full plan speed", res.data["explanation"])

    def test_speed_windows_need_configs_access(self):
        desk = User.objects.create_user(
            username="support", password="x", role=User.Role.SUPPORT, allowed_sections=["customers"]
        )
        self.client.force_authenticate(desk)
        self.assertEqual(self.client.get("/api/speed-windows/").status_code, 403)

    def test_a_window_whose_start_equals_its_end_is_refused(self):
        """It would never be on, and nothing on screen would say why."""
        res = self.client.post(
            "/api/speed-windows/",
            {"name": "Broken", "start_time": "22:00", "end_time": "22:00", "speed_pct": 200},
            format="json",
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("end_time", res.data)


class SafetyTests(TestCase):
    """The scheduled run fires for every connected customer at once, so
    its failure modes are network-wide by construction."""

    def setUp(self):
        self.tariff = Tariff.objects.create(
            name="10 Mbps", price=Decimal("599.00"),
            speed_download_kbps=10240, speed_upload_kbps=5120,
        )
        self.customer = Customer.objects.create(full_name="Everyone", email="e@x.com")
        self.services = [
            Service.objects.create(
                customer=self.customer, tariff=self.tariff, status=Service.Status.ACTIVE,
                start_date="2026-08-01", radius_username=f"line{i}",
            )
            for i in range(5)
        ]

    def _out(self, *args):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command("apply_speed_policies", *args, stdout=out)
        return out.getvalue()

    def test_seeding_records_the_baseline_without_contacting_anything(self):
        """Without this, the first real run treats every line on the
        network as changed, because last_pushed_rate_limit starts blank."""
        text = self._out("--seed")
        self.assertIn("Seeded 5", text)
        self.assertIn("No router was contacted", text)
        for service in self.services:
            service.refresh_from_db()
            self.assertEqual(service.last_pushed_rate_limit, "5120k/10240k")

    def test_after_seeding_a_run_finds_nothing_to_do(self):
        self._out("--seed")
        self.assertIn("0 changed · 5 already correct", self._out())

    def test_seeding_twice_lands_on_the_same_baseline(self):
        """Seeding is unconditional, so the count does not drop to zero --
        what matters is that the value is the same afterwards."""
        self._out("--seed")
        self._out("--seed")
        for service in self.services:
            service.refresh_from_db()
            self.assertEqual(service.last_pushed_rate_limit, "5120k/10240k")

    def test_seeding_CORRECTS_a_row_that_already_holds_a_wrong_value(self):
        """The case that made the previous fix useless. A row holding the
        boosted rate matched the target exactly, so the "already correct"
        check skipped it and the seed never corrected it -- re-seeding
        reported success and changed nothing."""
        SpeedWindow.objects.create(
            name="All day double", start_time=datetime.time(0, 0),
            end_time=datetime.time(23, 59), speed_pct=200,
        )
        # Exactly the state the earlier seeding bug left behind.
        Service.objects.update(last_pushed_rate_limit="10240k/20480k")
        self._out("--seed")
        for service in self.services:
            service.refresh_from_db()
            self.assertEqual(
                service.last_pushed_rate_limit, "5120k/10240k",
                "re-seeding left the stale boosted value in place",
            )

    def _connect_all(self):
        """Give every line an open accounting row, so they count as live.

        Offline lines are filtered out BEFORE the limit is applied -- there
        is no point spending the run's budget on lines that cannot be
        changed in place -- so a stampede test needs real sessions."""
        from radiusauth.models import RadAcct

        for i, service in enumerate(self.services):
            RadAcct.objects.create(
                acctsessionid=f"s{i}", acctuniqueid=f"u{i}", username=service.radius_username,
                nasipaddress="10.0.0.1", acctstarttime=timezone.now(),
                acctupdatetime=timezone.now(),
            )

    def test_the_limit_holds_back_a_stampede(self):
        """Every scenario where this job wants to touch hundreds of lines
        at once is a mistake. The cap turns "the whole network" into
        "some, then look at the log"."""
        self._connect_all()
        SpeedWindow.objects.create(
            name="All day double", start_time=datetime.time(0, 0), end_time=datetime.time(23, 59),
            speed_pct=200,
        )
        self._out("--seed")
        # Now change the policy so every line is due a push at once.
        SpeedWindow.objects.update(speed_pct=300)
        text = self._out("--limit", "2")
        self.assertIn("held back by --limit 2", text)
        self.assertIn("2 changed", text)

    def test_the_scheduled_run_never_disconnects_anybody(self):
        """apply_change's normal fallback is to drop the session. Doing
        that here would disconnect the whole customer base at 22:00 to
        deliver a boost nobody asked for."""
        import inspect

        from radiusauth.management.commands import apply_speed_policies

        source = inspect.getsource(apply_speed_policies)
        self.assertIn("allow_disconnect_fallback=False", source)

    def test_apply_change_without_the_fallback_reports_instead_of_dropping(self):
        from radiusauth.enforcement import apply_change

        service = self.services[0]
        # No live session, so it short-circuits before any of this -- the
        # point being that the option is accepted and the signature holds.
        self.assertTrue(apply_change(service, reason="tariff", allow_disconnect_fallback=False))


class ShaperAgreementTests(TestCase):
    """The RouterOS shaper and RADIUS must not disagree.

    Mikrotik-Rate-Limit builds a dynamic queue for the PPPoE session; the
    shaper pushes a separate static simple queue. The customer gets the
    more restrictive of the two -- so a shaper still reading the raw plan
    speed would hold a 4 Mbps queue against a line RADIUS had just burst
    to 8 Mbps. The boost is sent, accepted, and never felt, and nothing on
    either screen looks wrong.
    """

    def setUp(self):
        self.tariff = Tariff.objects.create(
            name="Skybre Wireless 4 Mbps", price=Decimal("399.00"),
            speed_download_kbps=4096, speed_upload_kbps=4096,
        )
        self.customer = Customer.objects.create(full_name="Bursting", email="b@x.com")
        self.service = Service.objects.create(
            customer=self.customer, tariff=self.tariff, status=Service.Status.ACTIVE,
            start_date="2026-08-01", radius_username="burst1",
        )

    def test_the_shaper_follows_the_window_too(self):
        from network.router_sync import effective_speed_kbps

        SpeedWindow.objects.create(
            name="All day double", start_time=datetime.time(0, 0), end_time=datetime.time(23, 59),
            speed_pct=200,
        )
        down, up = effective_speed_kbps(self.service)
        self.assertEqual(down, 8192)
        self.assertEqual(up, 8192)

    def test_the_shaper_and_radius_agree_exactly(self):
        from network.router_sync import effective_speed_kbps

        SpeedWindow.objects.create(
            name="All day double", start_time=datetime.time(0, 0), end_time=datetime.time(23, 59),
            speed_pct=200,
        )
        down, up = effective_speed_kbps(self.service)
        # Note the reversed order: the rate-limit string is upload/download.
        self.assertEqual(_mikrotik_rate_limit(self.service), f"{up}k/{down}k")

    def test_the_shaper_follows_fair_use_too(self):
        from network.router_sync import effective_speed_kbps

        self.tariff.fup_threshold_gb = 10
        self.tariff.fup_speed_pct = 25
        self.tariff.save()
        UsageBucket.objects.create(
            username="burst1",
            bucket_start=timezone.localtime().replace(day=1, hour=14, minute=0, second=0, microsecond=0),
            download_bytes=int(50 * 1024 ** 3), upload_bytes=0,
        )
        down, _ = effective_speed_kbps(self.service)
        self.assertEqual(down, 1024)  # 25% of 4096

    def test_a_tariff_with_no_speed_is_still_skipped(self):
        """(None, None) means "push no queue at all". Consulting the policy
        first would turn that into the fallback default and start pushing
        queues for services that deliberately have none."""
        from network.router_sync import effective_speed_kbps

        self.tariff.speed_download_kbps = None
        self.tariff.speed_upload_kbps = None
        self.tariff.save()
        self.service.refresh_from_db()
        self.assertEqual(effective_speed_kbps(self.service), (None, None))


class SeedBaselineTests(TestCase):
    """--seed records what the ROUTER has, not what we want it to have.

    Seeding while a window is open used to write down the BOOSTED rate as
    though it had already been delivered. The next run then saw nothing to
    do, the CoA was never sent, and the customer never got the burst --
    silent, and indistinguishable from the feature not working at all.
    """

    def setUp(self):
        self.tariff = Tariff.objects.create(
            name="Skybre Wireless 5Mbps", price=Decimal("286.35"),
            speed_download_kbps=5120, speed_upload_kbps=5120,
        )
        self.customer = Customer.objects.create(full_name="Test Line", email="t@x.com")
        self.service = Service.objects.create(
            customer=self.customer, tariff=self.tariff, status=Service.Status.ACTIVE,
            start_date="2026-08-01", radius_username="pppoe_test",
        )

    def _seed(self):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command("apply_speed_policies", "--seed", stdout=out)
        return out.getvalue()

    def test_seeding_inside_an_open_window_records_the_PLAN_rate(self):
        SpeedWindow.objects.create(
            name="SkybreWireless Burst", start_time=datetime.time(0, 0),
            end_time=datetime.time(23, 59), speed_pct=200,
        )
        self._seed()
        self.service.refresh_from_db()
        self.assertEqual(
            self.service.last_pushed_rate_limit, "5120k/5120k",
            "seeded the boosted rate, so the burst would never be pushed",
        )

    def test_and_the_next_run_therefore_still_has_work_to_do(self):
        """The behaviour that actually matters: after seeding mid-window,
        the boost must still be waiting to go out."""
        from io import StringIO

        from django.core.management import call_command

        from radiusauth.models import RadAcct

        SpeedWindow.objects.create(
            name="SkybreWireless Burst", start_time=datetime.time(0, 0),
            end_time=datetime.time(23, 59), speed_pct=200,
        )
        # Offline lines are filtered out before they are listed, so the
        # line needs a live session for this to exercise the real path.
        RadAcct.objects.create(
            acctsessionid="s1", acctuniqueid="u1", username="pppoe_test",
            nasipaddress="10.0.0.1", acctstarttime=timezone.now(),
            acctupdatetime=timezone.now(),
        )
        self._seed()
        out = StringIO()
        call_command("apply_speed_policies", stdout=out)
        self.assertIn("5120k/5120k -> 10240k/10240k", out.getvalue())

    def test_seeding_with_no_window_records_the_same_plan_rate(self):
        self._seed()
        self.service.refresh_from_db()
        self.assertEqual(self.service.last_pushed_rate_limit, "5120k/5120k")


class ShaperResyncTests(TestCase):
    """A CoA alone is not enough on a shaper-enabled device.

    Mikrotik-Rate-Limit updates the DYNAMIC queue for the session; the
    shaper maintains a separate STATIC simple queue. The customer gets the
    more restrictive of the two, so leaving the static queue at the plan
    speed means the boost is delivered, accepted, and never felt.
    """

    def test_the_command_resyncs_the_shaper_after_pushing(self):
        import inspect

        from radiusauth.management.commands import apply_speed_policies

        source = inspect.getsource(apply_speed_policies)
        self.assertIn("sync_device_shaper_queues", source)
        self.assertIn("enable_shaper", source)

    def test_a_shaper_failure_is_reported_and_not_fatal(self):
        """The CoA has already landed by then; the rest of the run must
        still finish."""
        import inspect

        from radiusauth.management.commands import apply_speed_policies

        source = inspect.getsource(apply_speed_policies)
        self.assertIn("couldn't re-sync shaper queues", source)
