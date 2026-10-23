"""What the Conecto integration must not get wrong.

The two failure modes that matter here are not "a field was mapped
oddly". They are:

  * an ECHO LOOP -- a ticket arrives from Conecto, our signal pushes it
    back, Conecto creates another, and so on. Several tests below exist
    only to pin that shut from both directions.
  * A PUSH THAT SILENTLY VANISHED -- Conecto retries nothing, so a
    failed call that was only logged is a customer who never hears back.
    Every failure must leave a replayable row.

Nothing here touches the network: requests is mocked throughout. A test
that needed Conecto's Cloud Run functions to be up would be a test that
stopped running the first time they were not.
"""
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from io import StringIO
from rest_framework.test import APIClient

from customers.models import Customer
from tickets.models import Ticket

from .mapping import priority_from_conecto, status_from_conecto, status_to_conecto
from .models import ConectoCall, ConectoLink, ConectoTicket

TOKEN = "test-shared-secret"

URLS = {
    "health": "https://example.invalid/health",
    "registerClientCode": "https://example.invalid/registerClientCode",
    "createTicket": "https://example.invalid/createTicket",
    "updateTicket": "https://example.invalid/updateTicket",
    "sendAlert": "https://example.invalid/sendAlert",
    "registerTown": "https://example.invalid/registerTown",
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
class WebhookAuthTests(TestCase):
    """The webhooks are the only part of this reachable by strangers."""

    def setUp(self):
        self.client = APIClient()

    def _post(self, token=None):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token is not None else {}
        return self.client.post(
            "/api/webhooks/conecto/client-registered/",
            {"client_code": "CUS-000001", "firebase_uid": "abc"},
            format="json",
            **headers,
        )

    def test_no_token_is_refused(self):
        self.assertEqual(self._post().status_code, 401)

    def test_a_wrong_token_is_refused(self):
        self.assertEqual(self._post("nearly-the-right-secret").status_code, 401)

    def test_nothing_is_written_by_a_refused_call(self):
        self._post("wrong")
        self.assertEqual(ConectoLink.objects.count(), 0)

    @override_settings(CONECTO_API_KEY="", CONECTO_WEBHOOK_TOKEN="")
    def test_an_unconfigured_platform_refuses_everything(self):
        """The dangerous default would be the other way round: a half
        finished install acting as an open ticket-creation endpoint."""
        self.assertEqual(self._post("").status_code, 401)
        self.assertEqual(self._post("anything").status_code, 401)


@CONFIGURED
class InboundTicketTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.customer = Customer.objects.create(
            full_name="Lani Grunder", email="lani@example.com", city="Nelspruit"
        )

    def _post(self, path, payload):
        return self.client.post(
            f"/api/webhooks/conecto/{path}/", payload, format="json",
            HTTP_AUTHORIZATION=f"Bearer {TOKEN}",
        )

    @patch("conecto.client.requests.request")
    def test_a_ticket_from_the_app_is_created_and_not_pushed_back(self, request):
        with self.captureOnCommitCallbacks(execute=True):
            res = self._post("ticket-created", {
                "ticket_id": "cn-1",
                "client_code": self.customer.customer_id,
                "subject": "No internet since this morning",
                "description": "Lights are on but nothing loads.",
                "category": "no_internet",
                "priority": "high",
            })

        self.assertEqual(res.status_code, 201, res.content)
        ticket = Ticket.objects.get()
        self.assertEqual(ticket.customer, self.customer)
        self.assertEqual(ticket.priority, Ticket.Priority.HIGH)
        self.assertEqual(ticket.department, Ticket.Department.SUPPORT)
        self.assertEqual(ticket.conecto.conecto_ticket_id, "cn-1")
        self.assertEqual(ticket.conecto.origin, ConectoTicket.Origin.CONECTO)

        # The whole point: no outbound call was made. Without the
        # suppression guard this is where the loop starts.
        request.assert_not_called()

    @patch("conecto.client.requests.request")
    def test_the_same_ticket_delivered_twice_makes_one_ticket(self, request):
        payload = {
            "ticket_id": "cn-1",
            "client_code": self.customer.customer_id,
            "subject": "Slow speeds",
        }
        with self.captureOnCommitCallbacks(execute=True):
            first = self._post("ticket-created", payload)
            second = self._post("ticket-created", payload)

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()["duplicate"])
        self.assertEqual(Ticket.objects.count(), 1)

    @patch("conecto.client.requests.request")
    def test_an_unknown_client_code_is_refused_rather_than_invented(self, request):
        res = self._post("ticket-created", {
            "ticket_id": "cn-2", "client_code": "CUS-999999", "subject": "Hello",
        })

        self.assertEqual(res.status_code, 404)
        self.assertEqual(Ticket.objects.count(), 0)
        # A webhook must never be able to manufacture customers.
        self.assertEqual(Customer.objects.count(), 1)

    @patch("conecto.client.requests.request")
    def test_closing_from_the_app_closes_our_ticket_without_pushing_back(self, request):
        ticket = Ticket.objects.create(customer=self.customer, subject="x")
        ConectoTicket.objects.create(
            ticket=ticket, conecto_ticket_id="cn-3",
            origin=ConectoTicket.Origin.CONECTO,
        )
        request.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            res = self._post("ticket-closed", {"ticket_id": "cn-3", "status": "closed"})

        self.assertEqual(res.status_code, 200, res.content)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, Ticket.Status.CLOSED)
        request.assert_not_called()

    @patch("conecto.client.requests.request")
    def test_a_ticket_we_raised_can_be_closed_by_our_own_number(self, request):
        """Conecto holds our ticket_number for tickets we pushed, so that
        is the handle it sends back."""
        ticket = Ticket.objects.create(customer=self.customer, subject="x")
        ConectoTicket.objects.create(
            ticket=ticket, conecto_ticket_id="cn-4", origin=ConectoTicket.Origin.SKYBRE
        )

        with self.captureOnCommitCallbacks(execute=True):
            res = self._post("ticket-closed", {"vexar_ticket_id": ticket.ticket_number})

        self.assertEqual(res.status_code, 200, res.content)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, Ticket.Status.CLOSED)

    def test_the_close_endpoint_will_not_reopen_anything(self):
        ticket = Ticket.objects.create(
            customer=self.customer, subject="x", status=Ticket.Status.CLOSED
        )
        ConectoTicket.objects.create(
            ticket=ticket, conecto_ticket_id="cn-5", origin=ConectoTicket.Origin.SKYBRE
        )

        res = self._post("ticket-closed", {"ticket_id": "cn-5", "status": "open"})

        self.assertEqual(res.status_code, 400)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, Ticket.Status.CLOSED)

    def test_registration_links_the_customer(self):
        res = self._post("client-registered", {
            "client_code": self.customer.customer_id,
            "firebase_uid": "fb-1",
            "email": "lani@example.com",
        })

        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(ConectoLink.objects.get().customer, self.customer)

    def test_registering_again_moves_the_link_rather_than_duplicating_it(self):
        """A reinstalled app produces a new Firebase uid for the same
        person; two rows for one customer would make 'is this customer on
        the app' unanswerable."""
        self._post("client-registered", {
            "client_code": self.customer.customer_id, "firebase_uid": "fb-1",
        })
        res = self._post("client-registered", {
            "client_code": self.customer.customer_id, "firebase_uid": "fb-2",
        })

        self.assertEqual(res.status_code, 200)
        link = ConectoLink.objects.get()
        self.assertEqual(link.firebase_uid, "fb-2")


@CONFIGURED
class OutboundTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(full_name="Direct Dave", city="Nelspruit")

    @patch("conecto.client.requests.request")
    def test_raising_a_ticket_here_pushes_it_and_stores_the_id(self, request):
        request.return_value = FakeResponse(payload={"ticket_id": "cn-9"})

        with self.captureOnCommitCallbacks(execute=True):
            ticket = Ticket.objects.create(customer=self.customer, subject="Fibre down")

        called = [c for c in request.call_args_list if "createTicket" in c.args[1]]
        self.assertEqual(len(called), 1)
        body = called[0].kwargs["json"]
        self.assertEqual(body["client_code"], self.customer.customer_id)
        self.assertEqual(body["vexar_ticket_id"], ticket.ticket_number)
        self.assertEqual(ticket.conecto.conecto_ticket_id, "cn-9")

    @patch("conecto.client.requests.request")
    def test_changing_the_status_pushes_an_update(self, request):
        request.return_value = FakeResponse(payload={"ticket_id": "cn-9"})
        with self.captureOnCommitCallbacks(execute=True):
            ticket = Ticket.objects.create(customer=self.customer, subject="Fibre down")
        request.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            ticket.status = Ticket.Status.RESOLVED
            ticket.save()

        self.assertEqual(request.call_count, 1)
        call = request.call_args
        self.assertEqual(call.args[0], "patch")
        # The id belongs in the URL path, not the body.
        self.assertTrue(call.args[1].endswith("/cn-9"))
        self.assertEqual(call.kwargs["json"], {"status": "closed"})

    @patch("conecto.client.requests.request")
    def test_a_save_that_changes_nothing_conecto_holds_pushes_nothing(self, request):
        """Tickets get saved by assignment, comments, the importer. A
        post_save that pushed on every one of those would spend a call
        per keystroke and re-send statuses nobody changed."""
        request.return_value = FakeResponse(payload={"ticket_id": "cn-9"})
        with self.captureOnCommitCallbacks(execute=True):
            ticket = Ticket.objects.create(customer=self.customer, subject="Fibre down")
        request.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            ticket.subject = "Fibre down at the tower"
            ticket.save()

        request.assert_not_called()

    @patch("conecto.client.requests.request")
    def test_a_ticket_that_arrived_from_conecto_is_never_updated_back_as_new(self, request):
        """Second half of the loop guard: even with the thread-local flag
        long gone, a ticket that already has a Conecto id is not created
        there again."""
        ticket = Ticket.objects.create(customer=self.customer, subject="From the app")
        ConectoTicket.objects.filter(ticket=ticket).delete()
        ConectoTicket.objects.create(
            ticket=ticket, conecto_ticket_id="cn-11", origin=ConectoTicket.Origin.CONECTO
        )
        request.reset_mock()
        request.return_value = FakeResponse(payload={})

        with self.captureOnCommitCallbacks(execute=True):
            ticket.status = Ticket.Status.PENDING
            ticket.save()

        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.args[0], "patch")

    @patch("conecto.client.requests.request")
    def test_conecto_being_down_does_not_break_the_ticket(self, request):
        """The thing this integration must never do is stop staff working
        because somebody else's Cloud Run function is unreachable."""
        import requests as requests_lib

        request.side_effect = requests_lib.ConnectionError("no route to host")

        with self.captureOnCommitCallbacks(execute=True):
            ticket = Ticket.objects.create(customer=self.customer, subject="Fibre down")

        self.assertTrue(Ticket.objects.filter(pk=ticket.pk).exists())
        call = ConectoCall.objects.get(endpoint="createTicket")
        self.assertEqual(call.status, ConectoCall.Status.FAILED)
        self.assertIn("no route to host", call.detail)

    @patch("conecto.client.requests.request")
    def test_a_rejected_push_is_recorded_with_its_reason(self, request):
        request.return_value = FakeResponse(status_code=403, text="Forbidden: bad key")

        with self.captureOnCommitCallbacks(execute=True):
            Ticket.objects.create(customer=self.customer, subject="Fibre down")

        call = ConectoCall.objects.get(endpoint="createTicket")
        self.assertEqual(call.http_status, 403)
        self.assertIn("bad key", call.detail)

    @override_settings(CONECTO_API_KEY="")
    @patch("conecto.client.requests.request")
    def test_nothing_is_sent_when_the_integration_is_switched_off(self, request):
        """A staging copy or a developer's laptop inherits this code. It
        must not start posting real customers at production Firebase."""
        with self.captureOnCommitCallbacks(execute=True):
            Ticket.objects.create(customer=self.customer, subject="x")

        request.assert_not_called()
        self.assertEqual(ConectoCall.objects.count(), 0)

    @patch("conecto.client.requests.request")
    def test_a_new_customer_is_registered_so_they_can_use_the_app(self, request):
        """Conecto refuses to register a subscriber whose client code it
        has never seen, so this push is what makes the app usable."""
        request.return_value = FakeResponse(payload={"success": True})

        with self.captureOnCommitCallbacks(execute=True):
            customer = Customer.objects.create(full_name="New Ned", city="White River")

        called = [c for c in request.call_args_list if "registerClientCode" in c.args[1]]
        self.assertEqual(len(called), 1)
        self.assertEqual(called[0].kwargs["json"]["client_code"], customer.customer_id)


@CONFIGURED
class ResyncTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(full_name="Direct Dave", city="Nelspruit")

    def _run(self, *args):
        out = StringIO()
        call_command("conecto_resync", *args, stdout=out, stderr=out)
        return out.getvalue()

    @patch("conecto.client.requests.request")
    def test_a_failed_push_is_replayed_and_finally_linked(self, request):
        request.side_effect = None
        request.return_value = FakeResponse(status_code=500, text="boom")
        with self.captureOnCommitCallbacks(execute=True):
            ticket = Ticket.objects.create(customer=self.customer, subject="Fibre down")
        self.assertFalse(ConectoTicket.objects.exists())

        request.return_value = FakeResponse(payload={"ticket_id": "cn-20"})
        self._run("--apply")

        ticket.refresh_from_db()
        self.assertEqual(ticket.conecto.conecto_ticket_id, "cn-20")
        self.assertEqual(
            ConectoCall.objects.filter(
                endpoint="createTicket", status=ConectoCall.Status.OK
            ).count(),
            1,
        )

    @patch("conecto.client.requests.request")
    def test_a_dry_run_sends_nothing(self, request):
        request.return_value = FakeResponse(status_code=500, text="boom")
        with self.captureOnCommitCallbacks(execute=True):
            Ticket.objects.create(customer=self.customer, subject="Fibre down")
        request.reset_mock()

        output = self._run()

        self.assertIn("Dry run", output)
        request.assert_not_called()


class MappingTests(TestCase):
    """The lossy edges, stated as tests so a later edit has to argue with
    them rather than quietly reverse them."""

    def test_resolved_and_closed_both_read_as_closed_to_a_subscriber(self):
        self.assertEqual(status_to_conecto(Ticket.Status.RESOLVED), "closed")
        self.assertEqual(status_to_conecto(Ticket.Status.CLOSED), "closed")

    def test_a_close_from_outside_is_closed_not_resolved(self):
        """Resolved is a claim that we fixed something. A subscriber
        tapping 'close' is not making that claim."""
        self.assertEqual(status_from_conecto("closed"), Ticket.Status.CLOSED)

    def test_an_unrecognised_status_is_ignored_rather_than_defaulted(self):
        """Defaulting to OPEN would let a typo REOPEN a closed ticket."""
        self.assertIsNone(status_from_conecto("Closed!"))
        self.assertIsNone(status_from_conecto(""))
        self.assertIsNone(status_from_conecto(None))

    def test_normal_comes_back_as_medium_not_low(self):
        """Inbound tickets must not land below what a phone call gets."""
        self.assertEqual(priority_from_conecto("normal"), Ticket.Priority.MEDIUM)
