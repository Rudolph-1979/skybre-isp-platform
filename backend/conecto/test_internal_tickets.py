"""Internal job-feedback tickets, and the one thing that must never happen.

A technician files "quote needed" or "stock needed" from a job in the
Conecto app. It becomes a ticket here that staff triage. It must never,
under any circumstance, reach a customer -- not in the portal, not as a
push notification on their phone.

Conecto told us on 2026-09-21 that they enforce nothing: internal and
client tickets share one Firestore collection and one id generator, and
addTicketReply writes a reply to whatever id it is handed. Their words:
"one guard only -- yours."

So the guards are structural rather than conditional, and these tests
attack the structure rather than the conditions:

  * an internal ticket has NO customer foreign key, so the portal's
    filter(customer=<profile>) cannot match it -- tested by deleting the
    deliberate filter's premise, not by trusting it;
  * a check constraint holds that true in both directions, so the hole
    cannot be opened later by somebody being helpful;
  * the Conecto id lives in a DIFFERENT TABLE, so the client reply path
    cannot be handed an internal id even by mistake.

Nothing here touches the network.
"""
import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.testing import ALL_SECTIONS
from customers.models import Customer
from scheduling.models import Job
from tickets.models import Ticket, TicketComment

from .models import ConectoInternalTicket, ConectoTicket

User = get_user_model()

TOKEN = "test-shared-secret"
URLS = {"addTicketReply": "https://example.invalid/addTicketReply"}

CONFIGURED = override_settings(
    CONECTO_API_KEY=TOKEN, CONECTO_WEBHOOK_TOKEN=TOKEN, CONECTO_URLS=URLS
)

CREATED = "/api/webhooks/conecto/internal-ticket-created/"
REPLIED = "/api/webhooks/conecto/internal-ticket-replied/"


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._payload


@CONFIGURED
class InternalTicketInboundTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.customer = Customer.objects.create(
            full_name="Lucille Slabbert", customer_id="CUS-004400", city="Kroonstad",
        )
        start = timezone.now().replace(microsecond=0)
        self.job = Job.objects.create(
            customer=self.customer, title="Fibre install", start=start,
            end=start + datetime.timedelta(hours=2),
        )

    def _post(self, path, payload, token=TOKEN):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token is not None else {}
        return self.client.post(path, payload, format="json", **headers)

    def _body(self, **overrides):
        body = {
            "conecto_ticket_id": "cn-int-1",
            "type": "internal",
            "job_id": None,
            "vexar_job_id": str(self.job.pk),
            "technician_code": "STF-000015",
            "subject": "No 2-core left on the bakkie",
            "description": "Need 100m before I can finish this one.",
            "category": "stock_needed",
            "status": "open",
            "created_at": timezone.now().isoformat(),
        }
        body.update(overrides)
        return body

    def _create(self, **overrides):
        with self.captureOnCommitCallbacks(execute=True):
            return self._post(CREATED, self._body(**overrides))

    # --- authentication --------------------------------------------------

    def test_an_unauthenticated_call_creates_nothing(self):
        self.assertEqual(self._post(CREATED, self._body(), token=None).status_code, 401)
        self.assertEqual(self._post(CREATED, self._body(), token="wrong").status_code, 401)
        self.assertEqual(Ticket.objects.count(), 0)

    # --- what it creates -------------------------------------------------

    @patch("conecto.client.requests.request")
    def test_feedback_becomes_a_ticket_with_no_customer(self, request):
        res = self._create()

        self.assertEqual(res.status_code, 201)
        ticket = Ticket.objects.get()
        self.assertTrue(ticket.is_internal)
        # The whole safety argument in one assertion.
        self.assertIsNone(ticket.customer_id)
        self.assertEqual(ticket.subject, "No 2-core left on the bakkie")

        link = ConectoInternalTicket.objects.get()
        self.assertEqual(link.job_id, self.job.pk)
        self.assertEqual(link.category, "stock_needed")

    @patch("conecto.client.requests.request")
    def test_it_is_never_pushed_back_to_conecto_as_a_client_ticket(self, request):
        """createTicket would make a second, CLIENT-FACING copy of a
        technician's private note -- in the app the customer reads."""
        self._create()

        request.assert_not_called()
        self.assertFalse(ConectoTicket.objects.exists())

    @patch("conecto.client.requests.request")
    def test_a_missing_subject_is_named_after_the_category(self, request):
        self._create(subject="", category="quote_needed")

        self.assertEqual(Ticket.objects.get().subject, "Quote needed")

    @patch("conecto.client.requests.request")
    def test_feedback_on_an_unknown_job_is_still_kept(self, request):
        """The technician still needs an answer. Losing the message because
        we cannot match a job id would be the worst possible trade."""
        res = self._create(vexar_job_id="999999", job_id=None)

        self.assertEqual(res.status_code, 201)
        self.assertIsNone(ConectoInternalTicket.objects.get().job_id)

    @patch("conecto.client.requests.request")
    def test_a_replay_does_not_create_a_second_ticket(self, request):
        self._create()
        second = self._create()

        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()["duplicate"])
        self.assertEqual(Ticket.objects.count(), 1)

    def test_it_is_refused_without_an_id(self):
        res = self._post(CREATED, self._body(conecto_ticket_id=""))

        self.assertEqual(res.status_code, 400)
        self.assertEqual(Ticket.objects.count(), 0)

    # --- inbound replies -------------------------------------------------

    @patch("conecto.client.requests.request")
    def test_a_technicians_reply_lands_in_the_thread(self, request):
        self._create()

        with self.captureOnCommitCallbacks(execute=True):
            res = self._post(REPLIED, {
                "conecto_ticket_id": "cn-int-1",
                "message": "Depot says Thursday.",
                "author_name": "Bernard",
                "created_at": timezone.now().isoformat(),
            })

        self.assertEqual(res.status_code, 201)
        comment = TicketComment.objects.get()
        self.assertEqual(comment.external_author, "Bernard")
        self.assertIsNone(comment.author_id)
        self.assertFalse(comment.is_internal)

    @patch("conecto.client.requests.request")
    def test_a_client_ticket_id_is_not_found_by_the_internal_receiver(self, request):
        """The two tables ARE the guard. Conecto's ids share one namespace,
        so nothing about the id itself says which kind it is -- only which
        table it is in."""
        ticket = Ticket.objects.create(customer=self.customer, subject="Slow line")
        ConectoTicket.objects.create(
            ticket=ticket, conecto_ticket_id="cn-client-1",
            origin=ConectoTicket.Origin.CONECTO,
        )

        res = self._post(REPLIED, {
            "conecto_ticket_id": "cn-client-1", "message": "wrong door",
        })

        self.assertEqual(res.status_code, 404)
        self.assertFalse(TicketComment.objects.exists())


class InternalTicketConstraintTests(TestCase):
    """The schema, not the code."""

    def setUp(self):
        self.customer = Customer.objects.create(
            full_name="Lucille Slabbert", customer_id="CUS-004401", city="Kroonstad",
        )

    def test_an_internal_ticket_cannot_be_given_a_customer(self):
        """Somebody being helpful -- "let's record which site it was" -- is
        how this guard would otherwise be dismantled a year from now."""
        ticket = Ticket.objects.create(is_internal=True, customer=None, subject="Stock")

        ticket.customer = self.customer
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ticket.save()

    def test_an_ordinary_ticket_cannot_lose_its_customer(self):
        """The other direction. A customer ticket orphaned to NULL would
        disappear from its own owner's portal."""
        ticket = Ticket.objects.create(customer=self.customer, subject="Slow line")

        ticket.customer = None
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ticket.save()


@CONFIGURED
class InternalTicketVisibilityTests(TestCase):
    """Who can see one, and who can never."""

    def setUp(self):
        self.client = APIClient()
        self.customer = Customer.objects.create(
            full_name="Lucille Slabbert", customer_id="CUS-004402", city="Kroonstad",
        )
        self.customer_user = User.objects.create_user(
            username="lucille", password="x", role=User.Role.CUSTOMER,
        )
        self.customer.user = self.customer_user
        self.customer.save()

        # allowed_sections is REQUIRED, not decoration. Empty does not mean
        # unrestricted for sections the way it does for partners -- a staff
        # account with none is refused by section_permission("tickets"), and
        # every existing tickets test grants ALL_SECTIONS for exactly this
        # reason. Without it the list call 403s, which is how this file
        # first failed.
        self.staff = User.objects.create_user(
            username="liesi", password="x", role=User.Role.SUPPORT, is_staff=True,
            allowed_sections=ALL_SECTIONS,
        )

        self.their_ticket = Ticket.objects.create(
            customer=self.customer, subject="Slow line",
        )
        self.feedback = Ticket.objects.create(
            is_internal=True, customer=None, subject="No stock",
        )
        ConectoInternalTicket.objects.create(
            ticket=self.feedback, conecto_ticket_id="cn-int-9",
        )

    def _numbers(self, response):
        # Status first, with the body as the message. Reading straight for
        # "results" turned a 403 into a KeyError that said nothing about
        # permissions -- which cost a deploy cycle to work out.
        self.assertEqual(response.status_code, 200, response.data)
        payload = response.data
        rows = payload["results"] if isinstance(payload, dict) else payload
        return {row["ticket_number"] for row in rows}

    def test_a_customer_never_sees_an_internal_ticket_in_the_list(self):
        self.client.force_authenticate(self.customer_user)

        numbers = self._numbers(self.client.get("/api/tickets/"))
        self.assertIn(self.their_ticket.ticket_number, numbers)
        self.assertNotIn(self.feedback.ticket_number, numbers)

    def test_a_customer_cannot_open_one_directly(self):
        """Ids are sequential and guessable, so the list filter is not the
        control that matters -- get_object() running off the same queryset
        is."""
        self.client.force_authenticate(self.customer_user)

        res = self.client.get(f"/api/tickets/{self.feedback.pk}/")
        self.assertEqual(res.status_code, 404)

    def test_a_customer_cannot_read_its_comments(self):
        TicketComment.objects.create(
            ticket=self.feedback, message="Depot says Thursday.",
            external_author="Bernard",
        )
        self.client.force_authenticate(self.customer_user)

        res = self.client.get(f"/api/ticket-comments/?ticket={self.feedback.pk}")
        payload = res.json()
        rows = payload["results"] if isinstance(payload, dict) else payload
        self.assertEqual(rows, [])

    def test_staff_do_see_it(self):
        """The failure that would go unnoticed. Everything above is about
        hiding it, and the easy way to pass all of those is to hide it from
        everybody -- leaving a technician waiting on an answer nobody was
        ever shown."""
        self.client.force_authenticate(self.staff)

        numbers = self._numbers(self.client.get("/api/tickets/"))
        self.assertIn(self.feedback.ticket_number, numbers)
        self.assertIn(self.their_ticket.ticket_number, numbers)

    def test_staff_cannot_make_an_existing_ticket_internal(self):
        """is_internal is read-only through the API. Writable, it would let
        somebody hide a conversation a customer was part of.

        This one PASSED before the staff account had sections -- because the
        PATCH was refused outright, so the field was unchanged for a reason
        that had nothing to do with the rule being tested. The status
        assertion below is what stops it going green on a 403 again.
        """
        self.client.force_authenticate(self.staff)

        res = self.client.patch(
            f"/api/tickets/{self.their_ticket.pk}/", {"is_internal": True}, format="json",
        )

        self.assertEqual(res.status_code, 200, res.data)
        self.their_ticket.refresh_from_db()
        self.assertFalse(self.their_ticket.is_internal)


@CONFIGURED
class InternalReplyOutboundTests(TestCase):
    """A staff answer going back to the technician."""

    def setUp(self):
        self.staff = User.objects.create_user(
            username="liesi", password="x", first_name="Liesl", last_name="Grunder",
            role=User.Role.SUPPORT, is_staff=True,
        )
        self.customer = Customer.objects.create(
            full_name="Lucille Slabbert", customer_id="CUS-004403", city="Kroonstad",
        )
        self.feedback = Ticket.objects.create(
            is_internal=True, customer=None, subject="No stock",
        )
        self.link = ConectoInternalTicket.objects.create(
            ticket=self.feedback, conecto_ticket_id="cn-int-4",
        )

    @patch("conecto.client.requests.request")
    def test_a_staff_reply_goes_out_under_the_real_name(self, request):
        """THE RULE INVERTS HERE. On a client ticket the author is masked to
        "Support Team", because our staff's names are not a customer's
        business. Conecto confirmed the name is shown verbatim in the push
        notification -- and a technician waiting on a stock answer should
        see who answered them."""
        request.return_value = FakeResponse(payload={})

        with self.captureOnCommitCallbacks(execute=True):
            TicketComment.objects.create(
                ticket=self.feedback, author=self.staff, message="Ordered, Thursday.",
            )

        self.assertEqual(request.call_count, 1)
        self.assertIn("cn-int-4", request.call_args.args[1])
        self.assertEqual(request.call_args.kwargs["json"]["author_name"], "Liesl Grunder")

    @patch("conecto.client.requests.request")
    def test_a_private_note_is_not_sent_to_the_technician(self, request):
        """On this kind of ticket "internal" means hidden from the
        TECHNICIAN, which is a different audience from the usual one and
        still has to be honoured."""
        with self.captureOnCommitCallbacks(execute=True):
            TicketComment.objects.create(
                ticket=self.feedback, author=self.staff, message="He asks every week.",
                is_internal=True,
            )

        request.assert_not_called()

    @patch("conecto.client.requests.request")
    def test_the_technicians_own_reply_is_not_echoed_back(self, request):
        with self.captureOnCommitCallbacks(execute=True):
            TicketComment.objects.create(
                ticket=self.feedback, author=None, message="Any news?",
                external_author="Bernard",
            )

        request.assert_not_called()

    @patch("conecto.client.requests.request")
    def test_a_client_reply_still_goes_out_masked(self, request):
        """The other side of the inversion, pinned so that making internal
        replies named does not quietly make client replies named too."""
        request.return_value = FakeResponse(payload={})
        ticket = Ticket.objects.create(customer=self.customer, subject="Slow line")
        ConectoTicket.objects.create(
            ticket=ticket, conecto_ticket_id="cn-client-4",
            origin=ConectoTicket.Origin.CONECTO,
        )

        with self.captureOnCommitCallbacks(execute=True):
            TicketComment.objects.create(
                ticket=ticket, author=self.staff, message="Looking into it.",
            )

        self.assertEqual(request.call_args.kwargs["json"]["author_name"], "Support Team")
