"""A reseller-restricted staff member must not see other partners' tickets.

Every other customer-linked list in the project is partner-scoped --
customers, billing, sales, scheduling, network, radiusauth, audit,
bankfeeds. Tickets were not: TicketViewSet.get_queryset was "if
user.is_staff_member: return qs", so a support agent restricted to one
reseller saw only that reseller's customers on the Customers page and
then every partner's tickets here -- subject, the customer's name, and
the whole comment thread the serializer nests. get_object() shares the
queryset, so they could reply to, reassign, close or delete a
competitor's ticket too.

TicketCommentViewSet had the same hole one hop further out. The thread is
fetched by /api/ticket-comments/?ticket=<id> and ticket ids are
sequential, so a guessed id handed over the conversation and the internal
staff notes on it -- the same reference-reuse leak tickets/models.py
_next_ticket_number already worries about, arriving by a different door.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from customers.models import Customer, Partner
from tickets.models import Ticket, TicketComment, TicketEscalation
from accounts.testing import ALL_SECTIONS

User = get_user_model()


class TicketPartnerScopingTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.acme = Partner.objects.create(name="Acme Fibre")
        self.rival = Partner.objects.create(name="Rival Networks")

        self.mine = Customer.objects.create(
            full_name="Acme Customer", email="ac@example.com", partner=self.acme
        )
        self.theirs = Customer.objects.create(
            full_name="Rival Customer", email="rc@example.com", partner=self.rival
        )
        self.direct = Customer.objects.create(
            full_name="Direct Customer", email="dc@example.com", partner=None
        )

        self.my_ticket = Ticket.objects.create(
            customer=self.mine, subject="Acme fault", description="No sync"
        )
        self.their_ticket = Ticket.objects.create(
            customer=self.theirs, subject="Rival fault", description="No sync"
        )
        self.direct_ticket = Ticket.objects.create(
            customer=self.direct, subject="Direct fault", description="No sync"
        )
        self.their_note = TicketComment.objects.create(
            ticket=self.their_ticket, message="Rival is 3 months in arrears", is_internal=True
        )

        self.restricted = User.objects.create_user(
            username="acme-agent", password="pw-for-tests",
            role=User.Role.SUPPORT, allowed_partners=[self.acme.id],
            allowed_sections=ALL_SECTIONS,
        )
        self.unrestricted = User.objects.create_user(
            username="hq-agent", password="pw-for-tests", role=User.Role.SUPPORT,
            allowed_sections=ALL_SECTIONS,
        )
        self.admin = User.objects.create_user(
            username="root", password="pw-for-tests",
            role=User.Role.ADMIN, allowed_partners=[self.acme.id],
        )

    def _subjects(self, user):
        self.client.force_authenticate(user)
        res = self.client.get("/api/tickets/?page_size=100")
        self.assertEqual(res.status_code, 200, res.data)
        return {row["subject"] for row in res.data["results"]}

    # ---- the list --------------------------------------------------------
    def test_tickets_are_partner_scoped(self):
        subjects = self._subjects(self.restricted)
        self.assertIn("Acme fault", subjects)
        self.assertNotIn("Rival fault", subjects)
    def test_another_partners_ticket_cannot_be_read(self):
        self.client.force_authenticate(self.restricted)
        res = self.client.get(f"/api/tickets/{self.their_ticket.id}/")
        self.assertEqual(res.status_code, 404)
    def test_another_partners_ticket_cannot_be_closed(self):
        self.client.force_authenticate(self.restricted)
        res = self.client.patch(
            f"/api/tickets/{self.their_ticket.id}/", {"status": "closed"}, format="json"
        )
        self.assertEqual(res.status_code, 404)
        self.their_ticket.refresh_from_db()
        self.assertEqual(self.their_ticket.status, Ticket.Status.OPEN)
    def test_another_partners_ticket_cannot_be_deleted(self):
        self.client.force_authenticate(self.restricted)
        res = self.client.delete(f"/api/tickets/{self.their_ticket.id}/")
        self.assertEqual(res.status_code, 404)
        self.assertTrue(Ticket.objects.filter(pk=self.their_ticket.pk).exists())
    def test_another_partners_comment_thread_cannot_be_read(self):
        """Scoping the tickets alone would have been cosmetic -- this is
        the endpoint the conversation actually comes from."""
        self.client.force_authenticate(self.restricted)
        res = self.client.get(f"/api/ticket-comments/?ticket={self.their_ticket.id}")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual([c["message"] for c in res.data["results"]], [])
    def test_another_partners_comment_cannot_be_edited(self):
        self.client.force_authenticate(self.restricted)
        res = self.client.patch(
            f"/api/ticket-comments/{self.their_note.id}/",
            {"message": "Account is clear, no action needed."},
            format="json",
        )
        self.assertEqual(res.status_code, 404)
        self.their_note.refresh_from_db()
        self.assertEqual(self.their_note.message, "Rival is 3 months in arrears")
    def test_a_direct_customers_ticket_needs_direct_ticked(self):
        """Direct customers (no partner) only when "Direct" is ticked for the
        user (Rudolph, 5 Oct 2026), exactly as on the customer list."""
        self.assertNotIn("Direct fault", self._subjects(self.restricted))
        self.restricted.allowed_partners = [*self.restricted.allowed_partners, 0]  # tick "Direct"
        self.restricted.save(update_fields=["allowed_partners"])
        self.assertIn("Direct fault", self._subjects(self.restricted))
    def test_unrestricted_staff_see_every_partner(self):
        subjects = self._subjects(self.unrestricted)
        self.assertIn("Acme fault", subjects)
        self.assertIn("Rival fault", subjects)
    def test_admin_sees_everything_despite_allowed_partners(self):
        self.assertIn("Rival fault", self._subjects(self.admin))
    # ---- the status-counts tile row --------------------------------------
    def test_status_counts_are_partner_scoped(self):
        """The Support Tickets tile row (TicketViewSet.status_counts) is
        computed off the same get_queryset() the list uses -- a restricted
        agent's "Open" tile must count only what they can actually see, or
        clicking it lands on a shorter list than the number promised."""
        self.client.force_authenticate(self.restricted)
        res = self.client.get("/api/tickets/status-counts/")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(
            set(res.data.keys()), {"open", "pending", "resolved", "closed", "escalated"}
        )
        # Acme's own ticket only -- not Rival's, and not the direct
        # customer's until "Direct" is ticked.
        self.assertEqual(res.data["open"], 1)
        self.restricted.allowed_partners = [*self.restricted.allowed_partners, 0]
        self.restricted.save(update_fields=["allowed_partners"])
        res = self.client.get("/api/tickets/status-counts/")
        self.assertEqual(res.data["open"], 2)
        self.assertEqual(res.data["closed"], 0)
    def test_status_counts_unrestricted_sees_every_partner(self):
        self.client.force_authenticate(self.unrestricted)
        res = self.client.get("/api/tickets/status-counts/")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["open"], 3)

    # ---- the Escalated tile / ?escalated=true filter -----------------------
    def test_escalated_count_and_filter_are_partner_scoped(self):
        """A pending escalation on a competitor's ticket must not show up
        in a restricted agent's Escalated tile, and must not leak into the
        list when they click it -- same discipline as status_counts."""
        TicketEscalation.objects.create(ticket=self.my_ticket)
        TicketEscalation.objects.create(ticket=self.their_ticket)
        self.client.force_authenticate(self.restricted)
        counts = self.client.get("/api/tickets/status-counts/")
        self.assertEqual(counts.status_code, 200, counts.data)
        self.assertEqual(counts.data["escalated"], 1)
        subjects = self._subjects_filtered(self.restricted, "escalated=true")
        self.assertEqual(subjects, {"Acme fault"})

    def test_escalated_count_unrestricted_sees_every_partner(self):
        TicketEscalation.objects.create(ticket=self.my_ticket)
        TicketEscalation.objects.create(ticket=self.their_ticket)
        self.client.force_authenticate(self.unrestricted)
        res = self.client.get("/api/tickets/status-counts/")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["escalated"], 2)

    def test_a_rejected_escalation_does_not_count_as_escalated(self):
        """Only a still-PENDING escalation means "needs attention now" --
        one NOC already decided is done, not outstanding."""
        TicketEscalation.objects.create(
            ticket=self.my_ticket, decision=TicketEscalation.Decision.REJECTED
        )
        self.client.force_authenticate(self.restricted)
        res = self.client.get("/api/tickets/status-counts/")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["escalated"], 0)

    def _subjects_filtered(self, user, query):
        self.client.force_authenticate(user)
        res = self.client.get(f"/api/tickets/?page_size=100&{query}")
        self.assertEqual(res.status_code, 200, res.data)
        return {row["subject"] for row in res.data["results"]}
    def test_my_own_partners_ticket_is_still_workable(self):
        # Not "resolved" -- since "only clients may resolve tickets" (see
        # tickets.test_escalation_workflow.TicketOnlyClientsResolveTests),
        # a plain staff member can no longer set that directly on a
        # customer ticket regardless of partner scoping. Priority is an
        # ordinary field with no such restriction, and is enough to prove
        # this is about scoping, not about the resolve workflow.
        self.client.force_authenticate(self.restricted)
        res = self.client.patch(
            f"/api/tickets/{self.my_ticket.id}/", {"priority": "urgent"}, format="json"
        )
        self.assertEqual(res.status_code, 200, res.data)
        self.my_ticket.refresh_from_db()
        self.assertEqual(self.my_ticket.priority, Ticket.Priority.URGENT)
    def test_a_portal_customer_still_sees_their_own_ticket_and_thread(self):
        """The ticket API is shared with the portal. scope_by_customer
        deliberately leaves non-staff alone; this is what says so."""
        login = User.objects.create_user(
            username="rival-cust", password="pw-for-tests", role=User.Role.CUSTOMER
        )
        self.theirs.user = login
        self.theirs.save()
        TicketComment.objects.create(
            ticket=self.their_ticket, message="A technician is booked for Thursday.",
            is_internal=False,
        )

        self.assertEqual(self._subjects(login), {"Rival fault"})
        res = self.client.get(f"/api/ticket-comments/?ticket={self.their_ticket.id}")
        messages = [c["message"] for c in res.data["results"]]
        self.assertIn("A technician is booked for Thursday.", messages)
        self.assertNotIn("Rival is 3 months in arrears", messages)
