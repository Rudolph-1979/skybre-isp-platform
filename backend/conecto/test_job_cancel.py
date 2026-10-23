"""Taking a job off a technician's phone.

Conecto shipped cancelJob on 2026-09-21. Before that there was no way to
withdraw one: cancel a job here, or move it to somebody else, and the
original technician kept it until they completed or ignored it. Every
test below is about one consequence of that -- a person driving to work
that is not happening -- and the three ways it used to occur.

The subtle one is the confirmation coming back. cancelJob fires a
job-status-changed webhook with status "cancelled", and a cancellation
only ever originates on this side, so that webhook confirms something we
did rather than instructing us. Applying it would be actively wrong for a
REASSIGNMENT, where the cancel we send for the old technician would
otherwise set our job to cancelled while the new technician is driving to
it.

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

from .models import ConectoJob, ConectoTechnician

User = get_user_model()

TOKEN = "test-shared-secret"

URLS = {
    "scheduleJob": "https://example.invalid/scheduleJob",
    "cancelJob": "https://example.invalid/cancelJob",
}

CONFIGURED = override_settings(
    CONECTO_API_KEY=TOKEN, CONECTO_WEBHOOK_TOKEN=TOKEN, CONECTO_URLS=URLS,
    CONECTO_DISPATCH_BY_STAFF_CODE=True,
)


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._payload


def endpoint_of(call_args):
    """Which Conecto function a mocked request was aimed at."""
    return call_args[0][1].rsplit("/", 1)[-1]


@CONFIGURED
class CancelTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(
            full_name="Lucille Slabbert", customer_id="CUS-001600",
            address="12 Kerk Street", city="Nelspruit",
        )
        self.tech = User.objects.create_user(
            username="christo", password="x", role="technician",
        )
        self.other = User.objects.create_user(
            username="zandre", password="x", role="technician",
        )
        self.start = timezone.now().replace(microsecond=0) + datetime.timedelta(days=1)

    def _dispatch(self, request):
        """Create a job that has actually been pushed, and return it."""
        request.return_value = FakeResponse(payload={"job_id": "cn-job-1"})
        with self.captureOnCommitCallbacks(execute=True):
            job = Job.objects.create(
                customer=self.customer, assigned_to=self.tech, title="Install",
                start=self.start, end=self.start + datetime.timedelta(hours=2),
            )
        request.reset_mock()
        request.return_value = FakeResponse(payload={"success": True})
        return job

    # --- the three ways a job leaves a phone ------------------------------

    def test_cancelling_a_job_withdraws_it(self):
        with patch("conecto.client.requests.request") as request:
            job = self._dispatch(request)

            with self.captureOnCommitCallbacks(execute=True):
                job.status = Job.Status.CANCELLED
                job.save()

        self.assertEqual(request.call_count, 1)
        self.assertEqual(endpoint_of(request.call_args), "cancelJob")
        body = request.call_args.kwargs["json"]
        self.assertEqual(body["conecto_job_id"], "cn-job-1")
        self.assertEqual(body["vexar_job_id"], str(job.pk))
        self.assertIn("reason", body)

    def test_unassigning_a_job_withdraws_it(self):
        with patch("conecto.client.requests.request") as request:
            job = self._dispatch(request)

            with self.captureOnCommitCallbacks(execute=True):
                job.assigned_to = None
                job.save()

        self.assertEqual(request.call_count, 1)
        self.assertEqual(endpoint_of(request.call_args), "cancelJob")

    def test_reassigning_cancels_the_old_and_schedules_the_new(self):
        """Conecto has no reassign call. Without the cancel, both
        technicians hold the job and the one who is no longer meant to do
        it has no way of knowing."""
        with patch("conecto.client.requests.request") as request:
            job = self._dispatch(request)

            with self.captureOnCommitCallbacks(execute=True):
                job.assigned_to = self.other
                job.save()

        endpoints = [endpoint_of(c) for c in request.call_args_list]
        self.assertEqual(endpoints, ["cancelJob", "scheduleJob"],
                         "the old job must be withdrawn before the new one goes out")

        scheduled = request.call_args_list[1].kwargs["json"]
        self.assertEqual(scheduled["staff_code"], self.other.staff_code)

        # And the row must not still say cancelled -- that describes the job
        # on the previous technician's phone. Left set, a later genuine
        # cancellation would be skipped as already done.
        self.assertEqual(ConectoJob.objects.get(job=job).last_status, "")

    def test_moving_a_job_to_a_non_technician_still_withdraws_it(self):
        """The job cannot go to them -- but it still has to come off the
        phone of the technician who had it."""
        boss = User.objects.create_user(username="jaco", password="x", role="admin")

        with patch("conecto.client.requests.request") as request:
            job = self._dispatch(request)

            with self.captureOnCommitCallbacks(execute=True):
                job.assigned_to = boss
                job.save()

        self.assertEqual([endpoint_of(c) for c in request.call_args_list], ["cancelJob"])

    # --- not cancelling twice ---------------------------------------------

    def test_a_cancelled_job_is_not_cancelled_again(self):
        """Conecto answers 409 for a job already cancelled, and a Job row is
        saved by plenty of unrelated code. Without this, every later save
        would send another cancellation and record another failure for
        something that had already worked."""
        with patch("conecto.client.requests.request") as request:
            job = self._dispatch(request)

            with self.captureOnCommitCallbacks(execute=True):
                job.status = Job.Status.CANCELLED
                job.save()
            self.assertEqual(request.call_count, 1)

            with self.captureOnCommitCallbacks(execute=True):
                job.description = "Customer called to confirm"
                job.save()

        self.assertEqual(request.call_count, 1, "no second cancellation")

    def test_a_completed_job_is_never_cancelled(self):
        with patch("conecto.client.requests.request") as request:
            job = self._dispatch(request)
            ConectoJob.objects.filter(job=job).update(last_status="completed")

            with self.captureOnCommitCallbacks(execute=True):
                job.status = Job.Status.CANCELLED
                job.save()

        request.assert_not_called()

    def test_a_failed_withdrawal_leaves_a_replayable_row(self):
        """The technician still has it. The only way anyone finds out is
        this row and the warning beside it."""
        from .models import ConectoCall

        with patch("conecto.client.requests.request") as request:
            job = self._dispatch(request)
            request.return_value = FakeResponse(500, text="upstream error")

            with self.captureOnCommitCallbacks(execute=True):
                job.status = Job.Status.CANCELLED
                job.save()

        row = ConectoCall.objects.get(endpoint="cancelJob")
        self.assertEqual(row.status, ConectoCall.Status.FAILED)
        self.assertEqual(row.subject_ref, str(job.pk))

        # And not marked cancelled, so a replay is still possible.
        self.assertNotEqual(ConectoJob.objects.get(job=job).last_status, "cancelled")

    # --- the confirmation coming back -------------------------------------

    def _status_webhook(self, job, status_value, **extra):
        body = {"vexar_job_id": str(job.pk), "status": status_value}
        body.update(extra)
        return APIClient().post(
            "/api/webhooks/conecto/job-status-changed/", body, format="json",
            HTTP_AUTHORIZATION=f"Bearer {TOKEN}",
        )

    def test_a_cancelled_confirmation_does_not_cancel_a_reassigned_job(self):
        """The one that matters. Reassigning sends a cancel for the old
        technician; Conecto confirms it with a cancelled webhook. Applying
        that would cancel a job the new technician is driving to."""
        with patch("conecto.client.requests.request") as request:
            job = self._dispatch(request)
            with self.captureOnCommitCallbacks(execute=True):
                job.assigned_to = self.other
                job.save()

        response = self._status_webhook(job, "cancelled", cancel_reason="Reassigned")

        self.assertEqual(response.status_code, 200, response.data)
        job.refresh_from_db()
        self.assertNotEqual(job.status, Job.Status.CANCELLED)

    def test_an_ordinary_status_still_applies(self):
        """The guard is about `cancelled` alone -- everything else Conecto
        reports is a technician's own progress and must still land."""
        with patch("conecto.client.requests.request") as request:
            job = self._dispatch(request)

        self._status_webhook(job, "checked_out")

        job.refresh_from_db()
        self.assertEqual(job.status, Job.Status.COMPLETED)

    def test_the_event_records_a_staff_code_when_that_is_what_arrives(self):
        """technician_code is null for everybody who registered with a
        staff code, which is all four of the real technicians."""
        from .models import ConectoJobEvent

        with patch("conecto.client.requests.request") as request:
            job = self._dispatch(request)

        self._status_webhook(
            job, "en_route", technician_code=None, staff_code=self.tech.staff_code,
        )

        event = ConectoJobEvent.objects.filter(job=job).latest("id")
        self.assertEqual(event.technician_code, self.tech.staff_code)

    def test_a_technician_code_still_wins_where_one_is_sent(self):
        from .models import ConectoJobEvent

        ConectoTechnician.objects.create(user=self.tech, technician_code="TECH-001")

        with patch("conecto.client.requests.request") as request:
            job = self._dispatch(request)

        self._status_webhook(
            job, "en_route", technician_code="TECH-001", staff_code=self.tech.staff_code,
        )

        event = ConectoJobEvent.objects.filter(job=job).latest("id")
        self.assertEqual(event.technician_code, "TECH-001")
