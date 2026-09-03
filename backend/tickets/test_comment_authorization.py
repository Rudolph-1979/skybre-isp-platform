"""What a customer may and may not do to a ticket thread.

The portal and the staff panel share one API. TicketCommentViewSet's
non-staff queryset scoped READS correctly -- the whole non-internal thread
on your own tickets, which is the point of the portal -- but that same
queryset was also the write scope, and `ticket` was a writable FK that
perform_create never checked.

So a customer could edit or delete the support agent's reply on their own
ticket (the text still rendering under the agent's name), and could post
a comment into any OTHER customer's thread by guessing a sequential
ticket id. The second one is an impersonation channel -- a message that
appears in a stranger's portal thread -- not just a data-integrity
problem.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from customers.models import Customer
from tickets.models import Ticket, TicketComment

User = get_user_model()


class TicketCommentAuthorizationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.agent = User.objects.create_user(
            username="agent1", password="pw-for-tests", role=User.Role.SUPPORT
        )
        self.alice_login = User.objects.create_user(
            username="alice", password="pw-for-tests", role=User.Role.CUSTOMER
        )
        self.alice = Customer.objects.create(
            full_name="Alice Ncube", email="alice@example.com", user=self.alice_login
        )
        self.bob_login = User.objects.create_user(
            username="bob", password="pw-for-tests", role=User.Role.CUSTOMER
        )
        self.bob = Customer.objects.create(
            full_name="Bob Mokoena", email="bob@example.com", user=self.bob_login
        )

        self.alice_ticket = Ticket.objects.create(
            customer=self.alice, subject="No internet since Tuesday", description="Dish looks fine"
        )
        self.bob_ticket = Ticket.objects.create(
            customer=self.bob, subject="Slow speeds", description="Evenings only"
        )
        self.agent_reply = TicketComment.objects.create(
            ticket=self.alice_ticket, author=self.agent,
            message="A technician is booked for Thursday morning.", is_internal=False,
        )

    # ---- a customer must not touch staff's words -------------------------

    def test_a_customer_cannot_edit_a_staff_reply_on_their_own_ticket(self):
        self.client.force_authenticate(self.alice_login)
        res = self.client.patch(
            f"/api/ticket-comments/{self.agent_reply.id}/",
            {"message": "Confirmed, your account is credited R2,400 and no payment is due."},
            format="json",
        )
        self.assertIn(res.status_code, (403, 404))
        self.agent_reply.refresh_from_db()
        self.assertEqual(self.agent_reply.message, "A technician is booked for Thursday morning.")

    def test_a_customer_cannot_delete_a_staff_reply(self):
        self.client.force_authenticate(self.alice_login)
        res = self.client.delete(f"/api/ticket-comments/{self.agent_reply.id}/")
        self.assertIn(res.status_code, (403, 404))
        self.assertTrue(TicketComment.objects.filter(pk=self.agent_reply.pk).exists())

    def test_a_customer_can_still_read_the_staff_reply(self):
        """The fix narrows writes only. Reading the thread is the portal."""
        self.client.force_authenticate(self.alice_login)
        res = self.client.get(f"/api/ticket-comments/?ticket={self.alice_ticket.id}")
        self.assertEqual(res.status_code, 200)
        messages = [c["message"] for c in res.data["results"]]
        self.assertIn("A technician is booked for Thursday morning.", messages)

    def test_a_customer_can_still_edit_their_own_comment(self):
        own = TicketComment.objects.create(
            ticket=self.alice_ticket, author=self.alice_login, message="Typo here", is_internal=False
        )
        self.client.force_authenticate(self.alice_login)
        res = self.client.patch(
            f"/api/ticket-comments/{own.id}/", {"message": "Fixed now"}, format="json"
        )
        self.assertEqual(res.status_code, 200, res.data)
        own.refresh_from_db()
        self.assertEqual(own.message, "Fixed now")

    # ---- a customer must not post into someone else's thread -------------

    def test_a_customer_cannot_comment_on_another_customers_ticket(self):
        self.client.force_authenticate(self.alice_login)
        res = self.client.post(
            "/api/ticket-comments/",
            {
                "ticket": self.bob_ticket.id,
                "message": "Skybre accounts here -- please EFT R2,400 to 62xxxxxxx.",
            },
            format="json",
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.bob_ticket.comments.count(), 0)

    def test_a_customer_can_still_comment_on_their_own_ticket(self):
        self.client.force_authenticate(self.alice_login)
        res = self.client.post(
            "/api/ticket-comments/",
            {"ticket": self.alice_ticket.id, "message": "Thursday works, thanks."},
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["author_name"], "alice")
        self.assertFalse(res.data["is_internal"])

    def test_a_customer_cannot_move_their_comment_onto_another_ticket(self):
        own = TicketComment.objects.create(
            ticket=self.alice_ticket, author=self.alice_login, message="Mine", is_internal=False
        )
        self.client.force_authenticate(self.alice_login)
        res = self.client.patch(
            f"/api/ticket-comments/{own.id}/", {"ticket": self.bob_ticket.id}, format="json"
        )
        own.refresh_from_db()
        self.assertEqual(own.ticket_id, self.alice_ticket.id)
        self.assertEqual(self.bob_ticket.comments.count(), 0)
        self.assertIn(res.status_code, (200, 400))

    def test_a_customer_cannot_hide_their_comment_from_the_portal(self):
        """perform_create forced is_internal=False; update did not."""
        own = TicketComment.objects.create(
            ticket=self.alice_ticket, author=self.alice_login, message="Mine", is_internal=False
        )
        self.client.force_authenticate(self.alice_login)
        self.client.patch(f"/api/ticket-comments/{own.id}/", {"is_internal": True}, format="json")
        own.refresh_from_db()
        self.assertFalse(own.is_internal)

    # ---- staff keep their powers -----------------------------------------

    def test_staff_can_still_edit_their_own_reply(self):
        self.client.force_authenticate(self.agent)
        res = self.client.patch(
            f"/api/ticket-comments/{self.agent_reply.id}/",
            {"message": "Technician booked for Friday instead."},
            format="json",
        )
        self.assertEqual(res.status_code, 200, res.data)

    def test_staff_can_still_post_an_internal_note(self):
        self.client.force_authenticate(self.agent)
        res = self.client.post(
            "/api/ticket-comments/",
            {"ticket": self.alice_ticket.id, "message": "Third fault this month", "is_internal": True},
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.data)
        self.assertTrue(res.data["is_internal"])

    def test_an_internal_note_stays_invisible_to_the_customer(self):
        TicketComment.objects.create(
            ticket=self.alice_ticket, author=self.agent, message="Do not credit", is_internal=True
        )
        self.client.force_authenticate(self.alice_login)
        res = self.client.get(f"/api/ticket-comments/?ticket={self.alice_ticket.id}")
        messages = [c["message"] for c in res.data["results"]]
        self.assertNotIn("Do not credit", messages)


class TicketOwnershipTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.agent = User.objects.create_user(
            username="agent2", password="pw-for-tests", role=User.Role.SUPPORT
        )
        self.alice_login = User.objects.create_user(
            username="alice2", password="pw-for-tests", role=User.Role.CUSTOMER
        )
        self.alice = Customer.objects.create(
            full_name="Alice Two", email="alice2@example.com", user=self.alice_login
        )
        self.bob = Customer.objects.create(full_name="Bob Two", email="bob2@example.com")
        self.ticket = Ticket.objects.create(
            customer=self.alice, subject="Mine", description="x"
        )

    def test_a_customer_cannot_move_their_ticket_to_another_customer(self):
        """This moved the ticket and its whole comment thread off their own
        account and onto a stranger's."""
        self.client.force_authenticate(self.alice_login)
        self.client.patch(f"/api/tickets/{self.ticket.id}/", {"customer": self.bob.id}, format="json")
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.customer_id, self.alice.id)

    def test_a_customer_cannot_choose_who_their_ticket_is_assigned_to(self):
        self.client.force_authenticate(self.alice_login)
        res = self.client.post(
            "/api/tickets/",
            {
                "customer": self.alice.id, "subject": "Urgent please",
                "description": "help", "assigned_to": self.agent.id,
            },
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.data)
        self.assertIsNone(Ticket.objects.get(pk=res.data["id"]).assigned_to_id)

    def test_a_customer_cannot_open_a_ticket_in_another_customers_name(self):
        """`customer` is a required writable field, so the override in
        perform_create is the only thing standing between a customer and
        filing a ticket against somebody else's account."""
        self.client.force_authenticate(self.alice_login)
        res = self.client.post(
            "/api/tickets/",
            {"customer": self.bob.id, "subject": "Not mine", "description": "x"},
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(Ticket.objects.get(pk=res.data["id"]).customer_id, self.alice.id)

    def test_staff_can_still_reassign_a_ticket(self):
        self.client.force_authenticate(self.agent)
        res = self.client.patch(
            f"/api/tickets/{self.ticket.id}/", {"customer": self.bob.id}, format="json"
        )
        self.assertEqual(res.status_code, 200, res.data)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.customer_id, self.bob.id)
