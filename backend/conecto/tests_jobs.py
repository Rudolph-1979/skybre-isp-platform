"""Job Dispatch: pushing work to a phone, and hearing back from it.

The failure modes here are not the same as the ticket integration's.

A JOB THAT DOES NOT MATCH THE CALENDAR is the dangerous one. A technician
drives to the address on their phone. If a reschedule or a change of
address doesn't follow, they drive to yesterday's job — so the tests
below pin that a real change pushes and an unrelated save does not.

AN UPDATE THAT ARRIVES AND IS DISCARDED is the other. Conecto has no
screen showing where anyone is and never will; these webhooks are the
only place a position, a distance or a time on site will ever exist. So
every update is recorded whole, including ones for jobs we cannot find
and statuses we do not understand.

Nothing here touches the network.
"""
import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from customers.models import Customer
from scheduling.models import Job

from .models import (
    ConectoJob, ConectoJobEvent, ConectoStockChecklist, ConectoTechnician,
)

User = get_user_model()
TOKEN = "test-shared-secret"

URLS = {
    "scheduleJob": "https://example.invalid/scheduleJob",
    "setStockChecklist": "https://example.invalid/setStockChecklist",
}

CONFIGURED = override_settings(
    CONECTO_API_KEY=TOKEN, CONECTO_WEBHOOK_TOKEN=TOKEN, CONECTO_URLS=URLS
)


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._payload


@CONFIGURED
class JobDispatchTests(TestCase):
    def setUp(self):
        self.tech = User.objects.create_user(
            username="christo", password="x", role="technician", first_name="Christo",
        )
        self.link = ConectoTechnician.objects.create(user=self.tech, technician_code="TECH-001")
        self.other = User.objects.create_user(username="zandre", password="x", role="technician")

        self.customer = Customer.objects.create(
            full_name="Lucille Slabbert", customer_id="CUS-001600", email="l@example.com",
            address="12 Kerk Street", city="Nelspruit",
        )
        self.start = timezone.now().replace(microsecond=0) + datetime.timedelta(days=1)

    def _job(self, **extra):
        return Job.objects.create(
            customer=self.customer, assigned_to=self.tech, title="Install",
            start=self.start, end=self.start + datetime.timedelta(hours=2), **extra,
        )

    # --- pushing ----------------------------------------------------------

    @patch("conecto.client.requests.request")
    def test_assigning_a_job_puts_it_on_the_technicians_list(self, request):
        request.return_value = FakeResponse(payload={"job_id": "cn-job-1"})

        with self.captureOnCommitCallbacks(execute=True):
            job = self._job()

        self.assertEqual(request.call_count, 1)
        body = request.call_args.kwargs["json"]
        self.assertEqual(body["technician_code"], "TECH-001")
        self.assertEqual(body["vexar_job_id"], str(job.pk))
        self.assertEqual(body["client_code"], "CUS-001600")
        self.assertEqual(ConectoJob.objects.get(job=job).conecto_job_id, "cn-job-1")

    @patch("conecto.client.requests.request")
    def test_a_job_with_no_location_uses_the_customers_address(self, request):
        """Job.location's help text has always promised this and nothing had
        implemented it. A job pushed with a blank address is one a
        technician cannot navigate to."""
        request.return_value = FakeResponse(payload={})

        with self.captureOnCommitCallbacks(execute=True):
            self._job()

        self.assertEqual(request.call_args.kwargs["json"]["address"], "12 Kerk Street, Nelspruit")

    @patch("conecto.client.requests.request")
    def test_an_explicit_location_wins(self, request):
        request.return_value = FakeResponse(payload={})

        with self.captureOnCommitCallbacks(execute=True):
            self._job(location="Tower site, R40 turnoff")

        self.assertEqual(request.call_args.kwargs["json"]["address"], "Tower site, R40 turnoff")

    @patch("conecto.client.requests.request")
    def test_rescheduling_pushes_the_new_time(self, request):
        request.return_value = FakeResponse(payload={})
        with self.captureOnCommitCallbacks(execute=True):
            job = self._job()
        request.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            job.start = self.start + datetime.timedelta(hours=3)
            job.save()

        self.assertEqual(request.call_count, 1)

    @patch("conecto.client.requests.request")
    def test_reassigning_pushes_to_the_new_technician(self, request):
        request.return_value = FakeResponse(payload={})
        ConectoTechnician.objects.create(user=self.other, technician_code="TECH-002")
        with self.captureOnCommitCallbacks(execute=True):
            job = self._job()
        request.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            job.assigned_to = self.other
            job.save()

        self.assertEqual(request.call_args.kwargs["json"]["technician_code"], "TECH-002")

    @patch("conecto.client.requests.request")
    def test_a_save_that_changes_nothing_a_technician_sees_pushes_nothing(self, request):
        """A push per save is a notification per save. Job rows get written
        by plenty of code that has nothing to do with the work."""
        request.return_value = FakeResponse(payload={})
        with self.captureOnCommitCallbacks(execute=True):
            job = self._job()
        request.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            job.job_type = Job.JobType.MAINTENANCE
            job.save()

        request.assert_not_called()

    @override_settings(CONECTO_DISPATCH_BY_STAFF_CODE=False)
    @patch("conecto.client.requests.request")
    def test_with_the_switch_off_a_technician_with_no_link_is_not_pushed(self, request):
        """`self.other` is a technician with a staff code and no hand-set
        Conecto link. With the staff-code switch off that is unreachable
        and nothing goes out; with it on -- which is production since
        2026-09-21 -- the staff code is exactly how they are reached.

        Pinned off rather than deleted: the off state is still a state
        this code has to get right."""
        with self.captureOnCommitCallbacks(execute=True):
            Job.objects.create(
                customer=self.customer, assigned_to=self.other, title="Install",
                start=self.start, end=self.start + datetime.timedelta(hours=1),
            )

        request.assert_not_called()

    @override_settings(CONECTO_DISPATCH_BY_STAFF_CODE=True)
    @patch("conecto.client.requests.request")
    def test_with_the_switch_on_a_technician_with_no_link_is_pushed(self, request):
        """The other half of the switch, so turning it off again would be
        noticed here rather than by a technician who stopped getting work."""
        request.return_value = FakeResponse(payload={"job_id": "cn-9"})

        with self.captureOnCommitCallbacks(execute=True):
            Job.objects.create(
                customer=self.customer, assigned_to=self.other, title="Install",
                start=self.start, end=self.start + datetime.timedelta(hours=1),
            )

        self.assertEqual(request.call_count, 1)
        self.assertEqual(
            request.call_args.kwargs["json"]["staff_code"], self.other.staff_code
        )

    @patch("conecto.client.requests.request")
    def test_an_unassigned_job_is_not_pushed(self, request):
        with self.captureOnCommitCallbacks(execute=True):
            Job.objects.create(
                customer=self.customer, title="Install",
                start=self.start, end=self.start + datetime.timedelta(hours=1),
            )

        request.assert_not_called()

    @override_settings(CONECTO_DISPATCH_BY_STAFF_CODE=True)
    @patch("conecto.client.requests.request")
    def test_a_deactivated_technician_stops_receiving_jobs(self, request):
        """THE ONE THAT CAUGHT A REAL BUG.

        Turning the staff-code switch on made this fail, and it was right
        to. Every technician has a staff code by construction, so the
        fallback quietly reached past a deactivated link and dispatched
        anyway -- meaning the single "stop sending this person jobs"
        control in the platform had started doing nothing, silently, with
        no error anywhere.

        The switch is forced ON here, because off it would pass for the
        wrong reason: there would be no fallback to reach past."""
        self.link.is_active = False
        self.link.save()

        with self.captureOnCommitCallbacks(execute=True):
            self._job()

        request.assert_not_called()

    @patch("conecto.client.requests.request")
    def test_conecto_being_down_does_not_break_the_job(self, request):
        import requests as requests_lib

        request.side_effect = requests_lib.ConnectionError("no route to host")

        with self.captureOnCommitCallbacks(execute=True):
            job = self._job()

        self.assertTrue(Job.objects.filter(pk=job.pk).exists())
        self.assertFalse(ConectoJob.objects.filter(job=job).exists())


@CONFIGURED
class JobStatusWebhookTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tech = User.objects.create_user(username="christo", password="x", role="technician")
        ConectoTechnician.objects.create(user=self.tech, technician_code="TECH-001")
        self.customer = Customer.objects.create(full_name="Lucille", customer_id="CUS-001600")
        self.start = timezone.now().replace(microsecond=0)
        self.job = Job.objects.create(
            customer=self.customer, assigned_to=self.tech, title="Install",
            start=self.start, end=self.start + datetime.timedelta(hours=2),
        )

    def _post(self, path, payload, token=TOKEN):
        return self.client.post(
            f"/api/webhooks/conecto/{path}/", payload, format="json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

    @patch("conecto.client.requests.request")
    def test_en_route_puts_the_job_in_progress(self, request):
        with self.captureOnCommitCallbacks(execute=True):
            res = self._post("job-status-changed", {
                "vexar_job_id": str(self.job.pk),
                "technician_code": "TECH-001",
                "status": "en_route",
                "timestamp": self.start.isoformat(),
                "lat": "-25.4753", "lng": "30.9694",
            })

        self.assertEqual(res.status_code, 200, res.content)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, Job.Status.IN_PROGRESS)
        # And nothing was pushed back: the update came FROM the phone.
        request.assert_not_called()

    def test_the_position_and_distance_are_kept(self):
        """Conecto has no screen showing where anyone is. If a status view is
        ever built here, these rows are the only thing it can be built
        from."""
        self._post("job-status-changed", {
            "vexar_job_id": str(self.job.pk),
            "technician_code": "TECH-001",
            "status": "checked_in",
            "lat": "-25.4753", "lng": "30.9694",
            "km_traveled": "14.8",
        })

        event = ConectoJobEvent.objects.get()
        self.assertEqual(str(event.lat), "-25.475300")
        self.assertEqual(str(event.km_traveled), "14.80")
        self.assertEqual(event.job, self.job)

    def test_completion_records_the_time_on_site(self):
        self._post("job-status-changed", {
            "vexar_job_id": str(self.job.pk), "status": "completed",
            "technician_code": "TECH-001", "duration_minutes": 95,
        })

        self.job.refresh_from_db()
        self.assertEqual(self.job.status, Job.Status.COMPLETED)
        self.assertEqual(ConectoJobEvent.objects.get().duration_minutes, 95)

    def test_an_update_for_an_unknown_job_is_still_recorded(self):
        """404 to Conecto, but the event is real and is kept: a technician
        genuinely drove somewhere, and the row is the only evidence."""
        res = self._post("job-status-changed", {
            "vexar_job_id": "999999", "status": "en_route", "technician_code": "TECH-001",
        })

        self.assertEqual(res.status_code, 404)
        self.assertEqual(ConectoJobEvent.objects.count(), 1)
        self.assertIsNone(ConectoJobEvent.objects.get().job)

    def test_an_unrecognised_status_is_recorded_but_changes_nothing(self):
        """Defaulting an unknown status would let a typo complete somebody's
        work, or reopen it."""
        self._post("job-status-changed", {
            "vexar_job_id": str(self.job.pk), "status": "tea_break",
            "technician_code": "TECH-001",
        })

        self.job.refresh_from_db()
        self.assertEqual(self.job.status, Job.Status.SCHEDULED)
        self.assertEqual(ConectoJobEvent.objects.get().status, "tea_break")

    def test_a_malformed_reading_does_not_cost_us_the_update(self):
        self._post("job-status-changed", {
            "vexar_job_id": str(self.job.pk), "status": "checked_in",
            "technician_code": "TECH-001", "km_traveled": "about 15", "lat": "",
        })

        event = ConectoJobEvent.objects.get()
        self.assertIsNone(event.km_traveled)
        self.assertEqual(event.status, "checked_in")

    def test_a_status_update_needs_the_token(self):
        res = self._post("job-status-changed", {"status": "en_route"}, token="wrong")

        self.assertEqual(res.status_code, 401)
        self.assertEqual(ConectoJobEvent.objects.count(), 0)


@CONFIGURED
class ChecklistTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.today = timezone.localdate()

    def _post(self, payload):
        return self.client.post(
            "/api/webhooks/conecto/job-checklist-completed/", payload, format="json",
            HTTP_AUTHORIZATION=f"Bearer {TOKEN}",
        )

    def test_a_completed_checklist_is_recorded_against_the_day(self):
        ConectoStockChecklist.objects.create(
            technician_code="TECH-001", effective_date=self.today,
            items=["Router", "Ladder"],
        )

        res = self._post({
            "technician_code": "TECH-001",
            "effective_date": self.today.isoformat(),
            "items": [{"item": "Router", "confirmed": True}, {"item": "Ladder", "confirmed": True}],
            "any_missing": False,
            "completed_at": timezone.now().isoformat(),
        })

        self.assertEqual(res.status_code, 200, res.content)
        checklist = ConectoStockChecklist.objects.get()
        self.assertFalse(checklist.any_missing)
        self.assertIsNotNone(checklist.completed_at)

    def test_missing_stock_is_flagged(self):
        """The field support acts on: somebody went out without a router."""
        res = self._post({
            "technician_code": "TECH-001",
            "effective_date": self.today.isoformat(),
            "items": [{"item": "Router", "confirmed": False}],
            "any_missing": True,
        })

        self.assertTrue(res.json()["any_missing"])
        self.assertTrue(ConectoStockChecklist.objects.get().any_missing)

    def test_any_missing_is_derived_when_conecto_omits_it(self):
        """It must not default to "all fine" -- that is the one value that
        silently means no follow-up."""
        self._post({
            "technician_code": "TECH-001",
            "effective_date": self.today.isoformat(),
            "items": [{"item": "Router", "confirmed": True}, {"item": "Ladder", "confirmed": False}],
        })

        self.assertTrue(ConectoStockChecklist.objects.get().any_missing)

    def test_a_completion_for_a_day_we_never_set_is_still_kept(self):
        res = self._post({
            "technician_code": "TECH-009",
            "effective_date": self.today.isoformat(),
            "items": [],
        })

        self.assertEqual(res.status_code, 200)
        self.assertEqual(ConectoStockChecklist.objects.count(), 1)

    def test_a_bad_date_is_refused(self):
        res = self._post({"technician_code": "TECH-001", "effective_date": "17-09-2026"})

        self.assertEqual(res.status_code, 400)

    @patch("conecto.client.requests.request")
    def test_setting_a_list_sends_it_and_records_it(self, request):
        request.return_value = FakeResponse(payload={"ok": True})
        user = User.objects.create_user(username="christo", password="x", role="technician")
        ConectoTechnician.objects.create(user=user, technician_code="TECH-001")

        from django.core.management import call_command
        from io import StringIO

        call_command(
            "conecto_set_checklist", "christo", "Router (dual-band)", "Ladder",
            "--apply", stdout=StringIO(), stderr=StringIO(),
        )

        body = request.call_args.kwargs["json"]
        self.assertEqual(body["technician_code"], "TECH-001")
        self.assertEqual(body["items"], ["Router (dual-band)", "Ladder"])
        self.assertEqual(
            ConectoStockChecklist.objects.get().items, ["Router (dual-band)", "Ladder"]
        )
