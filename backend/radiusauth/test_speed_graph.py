"""Speed-over-time graph (radiusauth.speed) and the "Used this month" fix."""
import datetime
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.testing import ALL_SECTIONS
from billing.models import Service, Tariff
from customers.models import Customer, Partner
from radiusauth import speed, usage
from radiusauth.models import RadAcct, SpeedBucket, UsageBucket

User = get_user_model()
MB = 1024 * 1024
UTC = datetime.timezone.utc


def utc(y, m, d, h=0, mi=0, s=0):
    return datetime.datetime(y, m, d, h, mi, s, tzinfo=UTC)


class BankSpeedTests(TestCase):
    def test_one_slot(self):
        speed.bank_speed("u1", utc(2026, 10, 5, 10, 1), utc(2026, 10, 5, 10, 2), download=1000, upload=100)
        row = SpeedBucket.objects.get()
        self.assertEqual(row.bucket_start, utc(2026, 10, 5, 10, 0))
        self.assertEqual((row.download_bytes, row.upload_bytes), (1000, 100))

    def test_spread_over_the_slots_it_covers_and_adds_up(self):
        # 10:03 -> 10:13 covers 2 min of the 10:00 slot, all of 10:05, 3 min of 10:10.
        speed.bank_speed("u1", utc(2026, 10, 5, 10, 3), utc(2026, 10, 5, 10, 13), download=1000, upload=0)
        rows = {r.bucket_start.minute: r.download_bytes for r in SpeedBucket.objects.all()}
        self.assertEqual(rows, {0: 200, 5: 500, 10: 300})
        self.assertEqual(sum(rows.values()), 1000)

    def test_adds_to_an_existing_slot(self):
        speed.bank_speed("u1", None, utc(2026, 10, 5, 10, 1), download=10, upload=0)
        speed.bank_speed("u1", None, utc(2026, 10, 5, 10, 2), download=5, upload=0)
        self.assertEqual(SpeedBucket.objects.get().download_bytes, 15)

    def test_prune(self):
        now = timezone.now()
        speed.bank_speed("u1", None, now - datetime.timedelta(days=9), download=10, upload=0)
        speed.bank_speed("u1", None, now, download=10, upload=0)
        speed.prune_speed_buckets(now)
        self.assertEqual(SpeedBucket.objects.count(), 1)


class SpeedSeriesTests(TestCase):
    now = utc(2026, 10, 5, 12, 2)  # last complete 5-min slot: 11:55-12:00

    def test_day_points_are_average_bps(self):
        # 37.5 MB in the 11:55 slot = 1 Mbps... 37_500_000 bytes * 8 / 300 = 1_000_000 bps.
        speed.bank_speed("u1", None, utc(2026, 10, 5, 11, 57), download=37_500_000, upload=3_750_000)
        s = speed.speed_series(["u1"], "day", now=self.now)
        self.assertEqual(len(s["points"]), 288)
        self.assertEqual(s["last"], {"download_bps": 1_000_000, "upload_bps": 100_000})
        self.assertEqual(s["max"]["download_bps"], 1_000_000)
        # Averaged over the whole 24 hours.
        self.assertEqual(s["avg"]["download_bps"], int(37_500_000 * 8 / 86400))

    def test_slot_in_progress_is_left_out(self):
        speed.bank_speed("u1", None, utc(2026, 10, 5, 12, 1), download=37_500_000, upload=0)
        s = speed.speed_series(["u1"], "day", now=self.now)
        self.assertEqual(s["max"]["download_bps"], 0)

    def test_hourly_fallback_before_detail_began(self):
        # Hour 08:00 only in UsageBucket (before 5-minute detail existed).
        usage.bank_usage("u1", utc(2026, 10, 5, 8, 30), download=450_000_000, upload=0)
        speed.bank_speed("u1", None, utc(2026, 10, 5, 11, 57), download=37_500_000, upload=0)
        s = speed.speed_series(["u1"], "day", now=self.now)
        eight = [p for p in s["points"] if p["at"].hour == 8]
        self.assertEqual(len(eight), 12)
        self.assertTrue(all(p["download_bps"] == 1_000_000 for p in eight))  # 450 MB/h = 1 Mbps
        self.assertEqual(s["detail_since"], utc(2026, 10, 5, 11, 55))

    def test_week_month_year_shapes(self):
        usage.bank_usage("u1", utc(2026, 10, 4, 9), download=900_000_000, upload=0)
        self.assertEqual(len(speed.speed_series(["u1"], "week", now=self.now)["points"]), 336)
        month = speed.speed_series(["u1"], "month", now=self.now)
        self.assertEqual(len(month["points"]), 372)
        self.assertEqual(month["max"]["download_bps"], 1_000_000)  # 900 MB over a 2h point
        year = speed.speed_series(["u1"], "year", now=self.now)
        self.assertEqual(len(year["points"]), 365)
        self.assertEqual(year["max"]["download_bps"], int(900_000_000 * 8 / 86400))

    def test_bad_range(self):
        with self.assertRaises(ValueError):
            speed.speed_series(["u1"], "fortnight")


class SamplerFeedsSpeedTests(TestCase):
    def _session(self, in_octets, out_octets):
        RadAcct.objects.update_or_create(
            acctuniqueid="s1",
            defaults=dict(
                acctsessionid="s1", username="u1", nasipaddress="10.0.0.1",
                acctstarttime=timezone.now(), acctupdatetime=timezone.now(),
                acctinputoctets=in_octets, acctoutputoctets=out_octets,
            ),
        )

    def test_same_delta_lands_in_both_tables(self):
        self._session(500 * MB, 900 * MB)
        call_command("sample_session_usage", stdout=StringIO())
        self._session(502 * MB, 910 * MB)
        call_command("sample_session_usage", stdout=StringIO())
        self.assertEqual(sum(SpeedBucket.objects.values_list("download_bytes", flat=True)), 10 * MB)
        self.assertEqual(sum(SpeedBucket.objects.values_list("upload_bytes", flat=True)), 2 * MB)
        self.assertEqual(UsageBucket.objects.get().download_bytes, 10 * MB)


class SpeedApiTests(TestCase):
    def setUp(self):
        self.acme = Partner.objects.create(name="Acme")
        self.rival = Partner.objects.create(name="Rival")
        self.t10 = Tariff.objects.create(name="10 Mbps", price=Decimal("1"), speed_download_kbps=10000, speed_upload_kbps=5000)
        self.t4 = Tariff.objects.create(name="4 Mbps", price=Decimal("1"), speed_download_kbps=4000, speed_upload_kbps=2000)
        self.customer = Customer.objects.create(full_name="Speedy", partner=self.acme)
        Service.objects.create(customer=self.customer, tariff=self.t10, status="active", start_date="2026-08-01", radius_username="line_a")
        Service.objects.create(customer=self.customer, tariff=self.t4, status="active", start_date="2026-08-01", radius_username="line_b")
        Service.objects.create(customer=self.customer, tariff=self.t4, status="terminated", start_date="2026-08-01", radius_username="old")
        self.staff = User.objects.create_user(username="staff", password="x", role="support", allowed_sections=ALL_SECTIONS)
        self.client = APIClient()
        self.client.force_authenticate(self.staff)

    def test_all_lines_and_plan(self):
        r = self.client.get(f"/api/customers/{self.customer.pk}/speed/?range=day")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual([l["username"] for l in r.data["lines"]], ["line_a", "line_b"])
        self.assertEqual(r.data["plan"], {"download_bps": 14_000_000, "upload_bps": 7_000_000})
        self.assertIsNone(r.data["line"])

    def test_one_line(self):
        r = self.client.get(f"/api/customers/{self.customer.pk}/speed/?range=week&line=line_b")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["plan"], {"download_bps": 4_000_000, "upload_bps": 2_000_000})
        self.assertEqual(r.data["line"], "line_b")

    def test_bad_range_and_partner_scoping(self):
        self.assertEqual(self.client.get(f"/api/customers/{self.customer.pk}/speed/?range=x").status_code, 400)
        rival_only = User.objects.create_user(
            username="r", password="x", role="support", allowed_sections=ALL_SECTIONS, allowed_partners=[self.rival.id],
        )
        self.client.force_authenticate(rival_only)
        self.assertEqual(self.client.get(f"/api/customers/{self.customer.pk}/speed/").status_code, 404)


class MonthTotalTests(TestCase):
    """"Used this month" read 32.5 MB beside 153 GB on the graph: an always-on
    session that started last month counted for nothing."""

    def test_accumulated_wins_when_larger(self):
        tariff = Tariff.objects.create(name="T", price=Decimal("1"), speed_download_kbps=1, speed_upload_kbps=1)
        customer = Customer.objects.create(full_name="Always On")
        Service.objects.create(customer=customer, tariff=tariff, status="active", start_date="2026-08-01", radius_username="ao")
        now = timezone.localtime()
        last_month = now.replace(day=1) - datetime.timedelta(days=2)
        RadAcct.objects.create(
            acctuniqueid="old", acctsessionid="old", username="ao", nasipaddress="10.0.0.1",
            acctstarttime=last_month, acctinputoctets=1 * MB, acctoutputoctets=50 * MB,
        )
        usage.bank_usage("ao", now, download=900 * MB, upload=40 * MB)
        data = usage.customer_usage(customer)
        self.assertEqual(data["download_bytes"], 900 * MB)
        self.assertEqual(data["totals_source"], "accumulated")
