"""Who lands on the "went offline" call list.

Most of these are about who must NOT be on it. A list that includes people we
suspended ourselves, or people whose line blipped for two minutes and came
back, is a list support stops reading.
"""
import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from billing.models import Service, Tariff
from customers.models import Customer, Partner
from radiusauth.models import RadAcct
from radiusauth.offline import recently_offline
from radiusauth.views import OfflineCustomersView

User = get_user_model()


class OfflineListTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.tariff = Tariff.objects.create(name="T", price=1, speed_download_kbps=1024,
                                            speed_upload_kbps=1024)
        self.staff = User.objects.create_user(username="boss", password="x", role="admin")

    def _customer(self, name, username, status=Customer.Status.ACTIVE,
                  service_status=Service.Status.ACTIVE, phone="0721234567"):
        customer = Customer.objects.create(
            full_name=name, email=f"{username}@x.com", phone=phone, status=status
        )
        Service.objects.create(customer=customer, tariff=self.tariff, status=service_status,
                               start_date="2026-08-01", radius_username=username)
        return customer

    def _session(self, username, started_hours_ago, stopped_hours_ago=None,
                 cause="Lost-Carrier", updated_hours_ago=None, uid=None):
        start = self.now - datetime.timedelta(hours=started_hours_ago)
        stop = self.now - datetime.timedelta(hours=stopped_hours_ago) if stopped_hours_ago is not None else None
        update = (
            self.now - datetime.timedelta(hours=updated_hours_ago)
            if updated_hours_ago is not None else (stop or start)
        )
        return RadAcct.objects.create(
            acctsessionid=uid or f"{username}-{started_hours_ago}",
            acctuniqueid=uid or f"{username}-{started_hours_ago}",
            username=username, nasipaddress="10.0.0.1",
            acctstarttime=start, acctstoptime=stop, acctupdatetime=update,
            acctterminatecause=cause if stop else None,
            framedipaddress="102.23.154.9",
        )

    def _run(self, hours=24):
        return recently_offline(list(Customer.objects.all()), hours=hours, now=self.now)

    # ---- who IS on the list ---------------------------------------------

    def test_a_line_that_dropped_and_stayed_down(self):
        self._customer("Down Dan", "dan")
        self._session("dan", started_hours_ago=8, stopped_hours_ago=3)
        rows = self._run()
        self.assertEqual([r["full_name"] for r in rows], ["Down Dan"])
        self.assertAlmostEqual(rows[0]["offline_seconds"], 3 * 3600, delta=5)
        self.assertIn("line dropped", rows[0]["terminate_reason"])
        self.assertTrue(rows[0]["clean_disconnect"])

    def test_contact_details_are_included(self):
        self._customer("Call Me", "callme", phone="0829998877")
        self._session("callme", 8, 3)
        row = self._run()[0]
        self.assertEqual(row["phone"], "0829998877")
        self.assertEqual(row["email"], "callme@x.com")
        self.assertEqual(row["last_ip"], "102.23.154.9")

    def test_a_session_that_died_without_saying_so_counts(self):
        """A CPE that loses power never sends Accounting-Stop, so the row stays
        open forever. These are the most broken lines of all and a naive query
        misses every one."""
        self._customer("Silent Sam", "sam")
        self._session("sam", started_hours_ago=10, stopped_hours_ago=None, updated_hours_ago=4)
        rows = self._run()
        self.assertEqual([r["full_name"] for r in rows], ["Silent Sam"])
        self.assertFalse(rows[0]["clean_disconnect"])
        self.assertIn("without disconnecting", rows[0]["terminate_reason"])

    def test_longest_down_first(self):
        self._customer("Recent Rita", "rita")
        self._customer("Ancient Amos", "amos")
        self._session("rita", 4, 1)
        self._session("amos", 20, 18)
        self.assertEqual([r["full_name"] for r in self._run()], ["Ancient Amos", "Recent Rita"])

    def test_flapping_is_counted(self):
        self._customer("Flappy Fred", "fred")
        for i in range(4):
            self._session("fred", started_hours_ago=10 - i, stopped_hours_ago=9 - i, uid=f"fred-{i}")
        self.assertEqual(self._run()[0]["drops_in_period"], 4)

    # ---- who must NOT be on it ------------------------------------------

    def test_someone_who_came_back_is_not_listed(self):
        self._customer("Back Bob", "bob")
        self._session("bob", started_hours_ago=8, stopped_hours_ago=6, uid="bob-old")
        # Reconnected and still reporting.
        self._session("bob", started_hours_ago=5, stopped_hours_ago=None,
                      updated_hours_ago=0, uid="bob-new")
        self.assertEqual(self._run(), [])

    def test_a_suspended_customer_is_not_listed(self):
        """They are offline because we suspended them. Ringing to ask if we can
        help would be asking a question we already know the answer to."""
        self._customer("Suspended Sue", "sue", status=Customer.Status.SUSPENDED)
        self._session("sue", 8, 3)
        self.assertEqual(self._run(), [])

    def test_a_suspended_service_is_not_listed(self):
        self._customer("Cut Colin", "colin", service_status=Service.Status.SUSPENDED)
        self._session("colin", 8, 3)
        self.assertEqual(self._run(), [])

    def test_someone_offline_longer_than_the_window_is_not_listed(self):
        self._customer("Gone Greg", "greg")
        self._session("greg", started_hours_ago=100, stopped_hours_ago=90)
        self.assertEqual(self._run(hours=24), [])
        self.assertEqual([r["full_name"] for r in self._run(hours=168)], ["Gone Greg"])

    def test_a_line_that_never_connected_is_not_listed(self):
        self._customer("New Nancy", "nancy")
        self.assertEqual(self._run(), [])

    # ---- the endpoint ----------------------------------------------------

    def _get(self, user, **params):
        request = APIRequestFactory().get("/x", params)
        force_authenticate(request, user=user)
        return OfflineCustomersView.as_view()(request)

    def test_endpoint_returns_the_list(self):
        self._customer("Down Dan", "dan")
        self._session("dan", 8, 3)
        response = self._get(self.staff)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["hours"], 24)

    def test_endpoint_respects_partner_scoping(self):
        mine = Partner.objects.create(name="Mine")
        theirs = Partner.objects.create(name="Theirs")
        customer = self._customer("Down Dan", "dan")
        customer.partner = theirs
        customer.save()
        self._session("dan", 8, 3)
        scoped = User.objects.create_user(username="reseller", password="x", role="support")
        scoped.allowed_partners = [mine.id]
        scoped.save()
        self.assertEqual(self._get(scoped).data["count"], 0)

    def test_endpoint_is_staff_only(self):
        customer_user = User.objects.create_user(username="cust", password="x", role="customer")
        self.assertEqual(self._get(customer_user).status_code, 404)

    def test_hours_is_clamped_not_trusted(self):
        self.assertEqual(self._get(self.staff, hours=100000).data["hours"], 168)
        self.assertEqual(self._get(self.staff, hours=0).data["hours"], 1)
        self.assertEqual(self._get(self.staff, hours="abc").status_code, 400)
