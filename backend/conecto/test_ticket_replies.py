"""Ticket replies, in both directions.

Before this existed the integration synced ticket STATE and none of the
conversation: a customer raised a ticket in the app, staff answered it in
the platform, and the answer went nowhere. Conecto's addTicketReply and
our ticket-replied webhook close that.

Almost every test here is about one sentence from Conecto: there is no
internal-note mode. Anything we send appears in the thread the client
reads and pushes a notification to their phone. So an internal note
reaching addTicketReply is not a bug that gets fixed in the next deploy
-- the customer has already read the handover note about them. The push
refuses internal notes, refuses comments that came FROM the client, and
refuses anything written inside the inbound webhook's suppressed() block,
and each of those refusals is tested on its own rather than trusting the
others to cover it.

The second theme is the echo loop the rest of this app is already careful
about: a reply arriving from Conecto must not be sent straight back to
Conecto as though we had written it.

Nothing here touches the network.

Every push goes through transaction.on_commit, and Django's TestCase
wraps each test in a transaction that is rolled back rather than
committed -- so without captureOnCommitCallbacks nothing fires. That
matters most for the tests asserting nothing was SENT, including the echo
test: they would pass against a mock that was never going to be called,
which is exactly the kind of green that hides the bug it was written for.
"""
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.testing import ALL_SECTIONS
from django.contrib.auth import get_user_model

from customers.models import Customer
from tickets.models import Ticket, TicketComment

from .models import ConectoCall, ConectoTicket

User = get_user_model()

TOKEN = "test-shared-secret"

URLS = {
    "health": "https://example.invalid/health",
    "registerClientCode": "https://example.invalid/registerClientCode",
    "createTicket": "https://example.invalid/createTicket",
    "updateTicket": "https://example.invalid/updateTicket",
    "sendAlert": "https://example.invalid/sendAlert",
    "registerTown": "https://example.invalid/registerTown",
    "addTicketReply": "https://example.invalid/addTicketReply",
}

CONFIGURED = override_settings(
    CONECTO_API_KEY=TOKEN, CONECTO_WEBHOOK_TOKEN=TOKEN, CONECTO_URLS=URLS
)

REPLIED_URL = "/api/webhooks/conecto/ticket-replied/"


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._payload


class RepliesTestCase(TestCase):
    """A ticket that Conecto knows about, and a staff user to answer it."""

    def setUp(self):
        self.customer = Customer.objects.create(
            customer_id="CUS-000001", full_name="Jan Botha", city="Kroonstad",
        )
        self.staff = User.objects.create_user(
            username="liesi", password="x", role="support", is_staff=True,
            allowed_sections=ALL_SECTIONS,
        )
        self.ticket = Ticket.objects.create(
            customer=self.customer, subject="No connection", description="Since 6am",
        )
        self.link = ConectoTicket.objects.create(
            ticket=self.ticket,
            conecto_ticket_id="aB3xY9qLmZpR2vN8tKj1",
            origin=ConectoTicket.Origin.CONECTO,
        )

    def _post_reply(self, **overrides):
        body = {
            "ticket_id": self.link.conecto_ticket_id,
            "message": "Still nothing, I restarted the router twice.",
            "author_name": "Jan Botha",
        }
        body.update(overrides)
        return APIClient().post(
            REPLIED_URL, body, format="json",
            HTTP_AUTHORIZATION=f"Bearer {TOKEN}",
        )


@CONFIGURED
class OutboundReplyTests(RepliesTestCase):
    """Staff answering in the platform reaches the client's phone."""

    def test_a_staff_reply_is_sent(self):
        with patch("conecto.client.requests.request") as request:
            request.return_value = FakeResponse(200, {"success": True, "replyId": "7fK2"})
            with self.captureOnCommitCallbacks(execute=True):
                TicketComment.objects.create(
                    ticket=self.ticket, author=self.staff, message="Technician booked for tomorrow.",
                )

        self.assertEqual(request.call_count, 1)
        _method, url = request.call_args[0]
        self.assertEqual(url, f"https://example.invalid/addTicketReply/{self.link.conecto_ticket_id}")
        self.assertEqual(
            request.call_args[1]["json"]["message"], "Technician booked for tomorrow."
        )

    def test_the_ticket_id_goes_in_the_path_not_the_body(self):
        """Conecto's function reads it from the URL. In the body it is
        ignored and the call 400s on a missing ticket id."""
        with patch("conecto.client.requests.request") as request:
            request.return_value = FakeResponse(200, {"success": True})
            with self.captureOnCommitCallbacks(execute=True):
                TicketComment.objects.create(
                    ticket=self.ticket, author=self.staff, message="On our way.",
                )

        self.assertIn(self.link.conecto_ticket_id, request.call_args[0][1])
        self.assertNotIn("ticket_id", request.call_args[1]["json"])

    def test_an_internal_note_is_never_sent(self):
        """The test this whole feature is built around. Conecto has no
        internal-note mode -- an internal note that reaches them is read
        by the customer it was written about, and cannot be recalled."""
        with patch("conecto.client.requests.request") as request:
            with self.captureOnCommitCallbacks(execute=True):
                TicketComment.objects.create(
                    ticket=self.ticket, author=self.staff, is_internal=True,
                    message="Third complaint this month, check if they are on the old CPE.",
                )

        request.assert_not_called()
        self.assertFalse(ConectoCall.objects.exists())

    def test_the_author_is_a_label_not_a_person(self):
        """Conecto shows staff replies to clients as a fixed label. Sending
        a username would put our staff's names on customers' phones."""
        with patch("conecto.client.requests.request") as request:
            request.return_value = FakeResponse(200, {"success": True})
            with self.captureOnCommitCallbacks(execute=True):
                TicketComment.objects.create(
                    ticket=self.ticket, author=self.staff, message="Booked.",
                )

        sent = request.call_args[1]["json"]["author_name"]
        self.assertEqual(sent, "Support Team")
        self.assertNotEqual(sent, self.staff.username)

    @override_settings(CONECTO_REPLY_AUTHOR_NAME="Skybre Support")
    def test_the_label_can_be_changed(self):
        with patch("conecto.client.requests.request") as request:
            request.return_value = FakeResponse(200, {"success": True})
            with self.captureOnCommitCallbacks(execute=True):
                TicketComment.objects.create(
                    ticket=self.ticket, author=self.staff, message="Booked.",
                )

        self.assertEqual(request.call_args[1]["json"]["author_name"], "Skybre Support")

    def test_editing_a_comment_does_not_re_send_it(self):
        """Otherwise correcting a typo delivers the message to the customer
        a second time."""
        with patch("conecto.client.requests.request") as request:
            request.return_value = FakeResponse(200, {"success": True})
            with self.captureOnCommitCallbacks(execute=True):
                comment = TicketComment.objects.create(
                    ticket=self.ticket, author=self.staff, message="Techician booked.",
                )
            with self.captureOnCommitCallbacks(execute=True):
                comment.message = "Technician booked."
                comment.save()

        self.assertEqual(request.call_count, 1)

    def test_a_ticket_conecto_does_not_know_about_sends_nothing(self):
        """A ticket raised in Skybre for a customer who does not use the
        app has no thread there to reply into."""
        other = Ticket.objects.create(customer=self.customer, subject="Billing query")

        with patch("conecto.client.requests.request") as request:
            with self.captureOnCommitCallbacks(execute=True):
                TicketComment.objects.create(
                    ticket=other, author=self.staff, message="Credited.",
                )

        request.assert_not_called()

    def test_a_failed_send_leaves_a_replayable_row(self):
        """Conecto retries nothing, so durability lives on our side or
        nowhere -- the same rule the rest of this app follows."""
        with patch("conecto.client.requests.request") as request:
            request.return_value = FakeResponse(500, text="upstream error")
            with self.captureOnCommitCallbacks(execute=True):
                TicketComment.objects.create(
                    ticket=self.ticket, author=self.staff, message="Booked.",
                )

        row = ConectoCall.objects.get(endpoint="addTicketReply")
        self.assertEqual(row.status, ConectoCall.Status.FAILED)
        self.assertEqual(row.subject_ref, self.ticket.ticket_number)


@CONFIGURED
class InboundReplyTests(RepliesTestCase):
    """The client answering in the app reaches the platform."""

    def test_a_reply_becomes_a_comment_on_the_ticket(self):
        response = self._post_reply()

        self.assertEqual(response.status_code, 201, response.data)
        comment = self.ticket.comments.get()
        self.assertEqual(comment.message, "Still nothing, I restarted the router twice.")
        self.assertFalse(comment.is_internal)
        self.assertIsNone(comment.author)
        self.assertEqual(comment.external_author, "Jan Botha")

    def test_an_inbound_reply_is_not_echoed_back(self):
        """The echo loop this app is careful about everywhere else: a reply
        from the customer pushed back to the customer as ours."""
        with patch("conecto.client.requests.request") as request:
            with self.captureOnCommitCallbacks(execute=True):
                self._post_reply()

        request.assert_not_called()

    def test_a_reply_with_no_author_is_still_attributed(self):
        """A message with no name beside it reads as though the platform
        wrote it, and staff answer accordingly."""
        response = self._post_reply(author_name="")

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(self.ticket.comments.get().external_author, "Customer")

    def test_it_can_be_matched_on_our_own_ticket_number(self):
        """The other handle Conecto holds, for tickets we pushed to them."""
        ConectoTicket.objects.all().delete()

        response = self._post_reply(
            ticket_id="", vexar_ticket_id=self.ticket.ticket_number
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(self.ticket.comments.count(), 1)

    def test_an_empty_message_is_refused(self):
        response = self._post_reply(message="   ")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.ticket.comments.count(), 0)

    def test_an_unknown_ticket_is_a_404(self):
        response = self._post_reply(ticket_id="not-a-real-id")

        self.assertEqual(response.status_code, 404)

    def test_the_same_reply_twice_is_stored_once(self):
        """Conecto logs and drops a failure rather than retrying, so a
        repeat is far more likely to be a duplicate delivery than a
        customer typing the identical sentence again."""
        self._post_reply()
        second = self._post_reply()

        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.data["duplicate"])
        self.assertEqual(self.ticket.comments.count(), 1)

    def test_the_payload_cannot_mark_a_customer_s_own_words_internal(self):
        """is_internal is our decision about our staff's writing. Taking it
        from the payload would let the app hide a customer's message from
        the customer -- and from the push that answers it."""
        response = self._post_reply(is_internal=True)

        self.assertEqual(response.status_code, 201, response.data)
        self.assertFalse(self.ticket.comments.get().is_internal)

    def test_a_bad_token_is_refused(self):
        response = APIClient().post(
            REPLIED_URL,
            {"ticket_id": self.link.conecto_ticket_id, "message": "hello"},
            format="json",
            HTTP_AUTHORIZATION="Bearer wrong",
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.ticket.comments.count(), 0)

    def test_replying_does_not_reopen_a_closed_ticket(self):
        """Deliberate, and worth revisiting as a product decision rather
        than as a side-effect of this endpoint: TicketClosedWebhook exists
        so the app cannot move a ticket to any state it likes, and
        reopening through here would go round that."""
        self.ticket.status = Ticket.Status.CLOSED
        self.ticket.save()

        self._post_reply()

        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, Ticket.Status.CLOSED)


@CONFIGURED
class RoundTripTests(RepliesTestCase):
    def test_a_conversation_does_not_grow_on_its_own(self):
        """Customer writes, staff answers, customer writes again. Exactly
        one outbound call: the staff reply. If the guards leak, this is
        where it shows as a thread that keeps talking to itself."""
        with patch("conecto.client.requests.request") as request:
            request.return_value = FakeResponse(200, {"success": True})

            with self.captureOnCommitCallbacks(execute=True):
                self._post_reply(message="It is still down.")
            with self.captureOnCommitCallbacks(execute=True):
                TicketComment.objects.create(
                    ticket=self.ticket, author=self.staff, message="Technician on the way.",
                )
            with self.captureOnCommitCallbacks(execute=True):
                self._post_reply(message="Thank you.")

        self.assertEqual(request.call_count, 1)
        self.assertEqual(self.ticket.comments.count(), 3)
        self.assertEqual(ConectoCall.objects.filter(endpoint="addTicketReply").count(), 1)
