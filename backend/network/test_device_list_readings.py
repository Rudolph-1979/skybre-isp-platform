"""Listing devices must not load the whole monitoring history.

The device serializer shows one reading per device -- the newest. The list
view prefetched the entire `readings` relation to produce it, so opening
Networking or Services pulled every MonitoringReading ever recorded for
every device on the page into memory. The poller appends to that table for
ever, so the cost grew without limit while the page still showed one row
per device.
"""
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from network.models import Device, MonitoringReading
from accounts.testing import ALL_SECTIONS

User = get_user_model()


class DeviceListReadingsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.staff = User.objects.create_user(
            username="netops", password="pw-for-tests", role=User.Role.TECHNICIAN,
            allowed_sections=ALL_SECTIONS,
        )
        self.client.force_authenticate(self.staff)
        self.device = Device.objects.create(
            name="Core", device_type="router", ip_address="10.0.0.1"
        )
        now = timezone.now()
        for minutes in range(40):
            MonitoringReading.objects.create(
                device=self.device,
                timestamp=now - timezone.timedelta(minutes=minutes),
                is_up=True, latency_ms=minutes,
            )

    def test_the_newest_reading_is_still_shown(self):
        response = self.client.get("/api/devices/?page_size=50")
        self.assertEqual(response.status_code, 200, response.data)
        reading = response.data["results"][0]["latest_reading"]
        self.assertIsNotNone(reading)
        self.assertEqual(reading["latency_ms"], 0, "the newest reading is the 0-minutes-ago one")

    def test_the_whole_reading_history_is_not_loaded_to_show_it(self):
        """Every query that touches the readings table must be bounded. The
        prefetch fetched all 40 rows here; on the live box that is every
        reading the poller has ever written."""
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get("/api/devices/?page_size=50")
        self.assertEqual(response.status_code, 200)

        reading_queries = [
            q["sql"] for q in captured.captured_queries
            if "monitoringreading" in q["sql"].lower()
        ]
        self.assertTrue(reading_queries, "expected the view to read the table at all")
        for sql in reading_queries:
            self.assertIn("LIMIT", sql.upper(),
                          f"an unbounded read of the monitoring history: {sql[:160]}")
