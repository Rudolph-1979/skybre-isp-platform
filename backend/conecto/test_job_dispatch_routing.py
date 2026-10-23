"""Who a job may be dispatched to, and what happens when it may not be.

Conecto confirmed on 2026-09-21 that scheduleJob does not check role. It
accepts a job for anybody, answers success, and stores it -- but a job
only reaches a phone through a car-session token, and that is refused to
every role except `technician`, in their Cloud Function and again in the
app itself.

So the failure this file exists for looks exactly like success: a 200, a
ConectoJob row saying pushed, and a job that will never appear on anyone's
phone. Nothing on either side raises anything. A dispatcher believes the
work is out; the work does not exist outside Skybre.

That is why the check lives on this side even though it is Conecto's rule,
and why refusing to send is the right answer rather than sending and
hoping. The tests below are mostly one assertion repeated in different
shapes: nothing goes out that cannot arrive.

The other half is the staff-code switch. scheduleJob currently matches
only a technicianCode that a Conecto admin sets by hand -- which is why
nothing has ever dispatched from this platform. Conecto will accept staff
codes, but have not shipped it, so the path is built and off. These tests
pin both states, because "off" has to stay genuinely off: sending a staff
code today would create a job attached to nobody.

Nothing here touches the network.
"""
import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from customers.models import Customer
from scheduling.models import Job

from .models import ConectoJob, ConectoTechnician

User = get_user_model()

TOKEN = "test-shared-secret"

URLS = {"scheduleJob": "https://example.invalid/scheduleJob"}

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
class DispatchRoutingTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(
            full_name="Lucille Slabbert", customer_id="CUS-001600",
            address="12 Kerk Street", city="Nelspruit",
        )
        self.start = timezone.now().replace(microsecond=0) + datetime.timedelta(days=1)

    def _user(self, username, role):
        return User.objects.create_user(username=username, password="x", role=role)

    def _job(self, assignee, **extra):
        return Job.objects.create(
            customer=self.customer, assigned_to=assignee, title="Install",
            start=self.start, end=self.start + datetime.timedelta(hours=2), **extra,
        )

    # --- who may receive a job -------------------------------------------

    def test_a_technician_with_a_linked_code_gets_it(self):
        tech = self._user("christo", "technician")
        ConectoTechnician.objects.create(user=tech, technician_code="TECH-001")

        with patch("conecto.client.requests.request") as request:
            request.return_value = FakeResponse(payload={"job_id": "cn-1"})
            with self.captureOnCommitCallbacks(execute=True):
                job = self._job(tech)

        self.assertEqual(request.call_count, 1)
        body = request.call_args.kwargs["json"]
        self.assertEqual(body["technician_code"], "TECH-001")
        self.assertNotIn("staff_code", body)
        self.assertTrue(ConectoJob.objects.filter(job_id=job.pk).exists())

    def test_an_admin_is_never_sent_a_job(self):
        """The whole reason this file exists. Conecto would accept it and
        answer success, and the job would reach nobody -- so a successful
        push would be recorded for work that does not exist outside
        Skybre."""
        boss = self._user("jaco", "admin")
        ConectoTechnician.objects.create(user=boss, technician_code="TECH-BOSS")

        with patch("conecto.client.requests.request") as request:
            with self.captureOnCommitCallbacks(execute=True):
                job = self._job(boss)

        request.assert_not_called()
        self.assertFalse(ConectoJob.objects.filter(job_id=job.pk).exists())

    def test_an_office_role_is_never_sent_a_job(self):
        """Support, sales and accounts map to Conecto's `staff` role, which
        cannot obtain a car session either."""
        for username, role in (("liesi", "support"), ("anton", "sales"), ("janine", "accounts")):
            with self.subTest(role=role):
                person = self._user(username, role)

                with patch("conecto.client.requests.request") as request:
                    with self.captureOnCommitCallbacks(execute=True):
                        self._job(person)

                request.assert_not_called()

    def test_an_unassigned_job_is_not_sent(self):
        with patch("conecto.client.requests.request") as request:
            with self.captureOnCommitCallbacks(execute=True):
                self._job(None)

        request.assert_not_called()

    @override_settings(CONECTO_DISPATCH_BY_STAFF_CODE=False)
    def test_with_the_switch_off_a_staff_code_is_not_routable(self):
        """This was the world until 2026-09-21: four technicians with staff
        codes and no hand-set technicianCode, and nothing dispatching --
        correctly, because a job sent then would have attached to nobody.

        Pinned with the switch explicitly OFF rather than deleted. The
        setting is now on in production, so without the override this test
        silently changed meaning and started asserting that a job we now
        DO send is not sent. It failed, which is the system working; it is
        kept because the off state still has to stay genuinely off for any
        deployment that has not enabled it."""
        tech = self._user("fanie", "technician")
        self.assertTrue(tech.staff_code)

        with patch("conecto.client.requests.request") as request:
            with self.captureOnCommitCallbacks(execute=True):
                self._job(tech)

        request.assert_not_called()

    # --- the staff-code switch -------------------------------------------

    @override_settings(CONECTO_DISPATCH_BY_STAFF_CODE=True)
    def test_the_switch_sends_the_staff_code(self):
        tech = self._user("zandre", "technician")

        with patch("conecto.client.requests.request") as request:
            request.return_value = FakeResponse(payload={"job_id": "cn-2"})
            with self.captureOnCommitCallbacks(execute=True):
                self._job(tech)

        body = request.call_args.kwargs["json"]
        self.assertEqual(body["staff_code"], tech.staff_code)
        self.assertNotIn("technician_code", body)

    @override_settings(CONECTO_DISPATCH_BY_STAFF_CODE=True)
    def test_a_linked_technician_code_still_wins(self):
        """Somebody who registered on a hand-set technicianCode is known to
        Conecto by that code. Switching the platform over must not change
        the identity of a technician already receiving work."""
        tech = self._user("christo", "technician")
        ConectoTechnician.objects.create(user=tech, technician_code="TECH-001")

        with patch("conecto.client.requests.request") as request:
            request.return_value = FakeResponse(payload={"job_id": "cn-3"})
            with self.captureOnCommitCallbacks(execute=True):
                self._job(tech)

        body = request.call_args.kwargs["json"]
        self.assertEqual(body["technician_code"], "TECH-001")
        self.assertNotIn("staff_code", body)

    @override_settings(CONECTO_DISPATCH_BY_STAFF_CODE=True)
    def test_the_switch_does_not_let_a_non_technician_through(self):
        """The switch is about WHICH code identifies a technician, not
        about who may be dispatched to."""
        boss = self._user("jaco", "admin")

        with patch("conecto.client.requests.request") as request:
            with self.captureOnCommitCallbacks(execute=True):
                self._job(boss)

        request.assert_not_called()

    @override_settings(CONECTO_DISPATCH_BY_STAFF_CODE=True)
    def test_an_inactive_link_stops_dispatch_entirely(self):
        """THIS TEST USED TO ASSERT THE OPPOSITE, and it was wrong.

        It was written the same day as the staff-code switch and said an
        inactive link should fall through to the staff code -- with no
        reason given, which is the tell. Meanwhile
        tests_jobs.test_a_deactivated_technician_stops_receiving_jobs said
        the reverse. Two tests in one app, both mine, asserting opposite
        things about the same input; the one with no justification was
        the one describing the riskier behaviour.

        ConectoTechnician.is_active is THE control for "this person has
        stopped doing field work" -- the model says so, and
        conecto_link_technician --remove prints it. Since every technician
        has a staff code by construction, a fall-through made that control
        do nothing at all, silently. An explicit off beats an implicit
        fallback.
        """
        tech = self._user("zandre", "technician")
        ConectoTechnician.objects.create(
            user=tech, technician_code="TECH-OLD", is_active=False,
        )

        with patch("conecto.client.requests.request") as request:
            with self.captureOnCommitCallbacks(execute=True):
                self._job(tech)

        request.assert_not_called()

    @override_settings(CONECTO_DISPATCH_BY_STAFF_CODE=True)
    def test_a_blocking_record_stops_a_technician_who_never_had_a_code(self):
        """The case --remove could not reach before.

        A link row with NO code, switched off, exists only to say "not
        this person". Without it, --remove was a no-op for anybody who had
        only ever had a staff code, while still printing that they were
        deactivated.
        """
        tech = self._user("fanie", "technician")
        ConectoTechnician.objects.create(user=tech, technician_code="", is_active=False)

        with patch("conecto.client.requests.request") as request:
            with self.captureOnCommitCallbacks(execute=True):
                self._job(tech)

        request.assert_not_called()

    @override_settings(CONECTO_DISPATCH_BY_STAFF_CODE=True)
    def test_two_people_can_be_blocked_at_once(self):
        """Uniqueness on technician_code used to be a plain column
        constraint, and Postgres allows only ONE empty string in one of
        those. So the second blocking record would have raised an
        IntegrityError from a command about nothing of the sort."""
        for name in ("fanie", "zandre"):
            ConectoTechnician.objects.create(
                user=self._user(name, "technician"), technician_code="", is_active=False,
            )

        self.assertEqual(ConectoTechnician.objects.filter(technician_code="").count(), 2)

    @override_settings(CONECTO_DISPATCH_BY_STAFF_CODE=True)
    def test_an_active_link_with_no_code_still_uses_the_staff_code(self):
        """A blocking record that has been switched back on, but not
        deleted. It should not block, and it has no code to route by, so
        the staff code is the answer -- not silence."""
        tech = self._user("christo", "technician")
        ConectoTechnician.objects.create(user=tech, technician_code="", is_active=True)

        with patch("conecto.client.requests.request") as request:
            request.return_value = FakeResponse(payload={"job_id": "cn-6"})
            with self.captureOnCommitCallbacks(execute=True):
                self._job(tech)

        self.assertEqual(request.call_args.kwargs["json"]["staff_code"], tech.staff_code)

    # --- reassignment -----------------------------------------------------

    def test_moving_a_job_to_a_non_technician_withdraws_it(self):
        """It used to be that the original technician simply kept it,
        because Conecto had no withdraw endpoint and all we could do was
        log a warning. cancelJob shipped on 2026-09-21, so the job now
        comes off their phone.

        This test asserted the old behaviour and failed when the better
        one arrived -- worth saying plainly, because a test that fails
        because the product improved looks identical, in a list of
        failures, to one that fails because the product broke. The
        difference is only ever visible by reading it."""
        tech = self._user("christo", "technician")
        ConectoTechnician.objects.create(user=tech, technician_code="TECH-001")
        boss = self._user("jaco", "admin")

        with patch("conecto.client.requests.request") as request:
            request.return_value = FakeResponse(payload={"job_id": "cn-5"})
            with self.captureOnCommitCallbacks(execute=True):
                job = self._job(tech)
            self.assertEqual(request.call_count, 1)

            with self.captureOnCommitCallbacks(execute=True):
                job.assigned_to = boss
                job.save()

        self.assertEqual(request.call_count, 2, "the job should be withdrawn")
        # The second call is the withdrawal, and NOT a second schedule to
        # somebody who cannot receive it. The URL is positional in
        # client.call's requests.request(method, target, ...), hence args.
        self.assertIn("cancelJob", request.call_args.args[1])

    def test_the_client_refuses_a_call_with_no_code_at_all(self):
        """Belt and braces below the signal: if anything ever calls
        schedule_job directly, it still cannot create a job attached to
        nobody."""
        from conecto import client

        tech = self._user("christo", "technician")
        job = self._job(tech)

        with patch("conecto.client.requests.request") as request:
            result = client.schedule_job(job)

        self.assertIsNone(result)
        request.assert_not_called()
