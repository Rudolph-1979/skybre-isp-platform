"""A ticket reference must belong to one ticket, forever.

`ticket_number` was `TCK-{max(id) + 1}` on a unique column, which had two
faults.

It reused numbers: the maximum came off an existing ROW, so deleting the
newest ticket handed its number straight to the next one created. A
customer quoting the reference they were given then reached a stranger's
ticket and saw somebody else's comment thread.

And it had no retry: two simultaneous creates computed the same number and
the loser got an unhandled IntegrityError, i.e. a 500 on a save that was
perfectly valid. Both Customer.save() and Invoice.save() carry a
five-attempt loop for this identical race.

billing.IssuedNumberHighWater already existed for exactly this on
invoices -- "the fact being recorded is about the sequence... it has to
outlive every document in it" -- so tickets now use it too.
"""
from django.test import TestCase, TransactionTestCase

from billing.models import IssuedNumberHighWater
from customers.models import Customer
from tickets.models import Ticket


class TicketNumberTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(full_name="Ticket Tessa", email="t@example.com")

    def _ticket(self, subject="Line down"):
        return Ticket.objects.create(customer=self.customer, subject=subject, description="x")

    def test_numbers_start_at_one_and_increment(self):
        self.assertEqual(self._ticket().ticket_number, "TCK-000001")
        self.assertEqual(self._ticket().ticket_number, "TCK-000002")

    def test_a_deleted_tickets_number_is_never_reissued(self):
        """The core of it: a customer holding TCK-000002 must not be sent
        to somebody else's thread."""
        first = self._ticket("first")
        second = self._ticket("second")
        reused = second.ticket_number
        second.delete()

        third = self._ticket("third")
        self.assertNotEqual(third.ticket_number, reused)
        self.assertNotEqual(third.ticket_number, first.ticket_number)

    def test_deleting_every_ticket_does_not_restart_the_sequence(self):
        numbers = {self._ticket(f"t{i}").ticket_number for i in range(3)}
        Ticket.objects.all().delete()
        fresh = self._ticket("after the purge").ticket_number
        self.assertNotIn(fresh, numbers)

    def test_the_high_water_mark_tracks_the_sequence(self):
        self._ticket()
        latest = self._ticket()
        mark = IssuedNumberHighWater.objects.get(prefix="TCK")
        self.assertEqual(mark.last_seq, int(latest.ticket_number.rpartition("-")[2]))

    def test_numbering_stays_gapless_in_normal_use(self):
        """The mark is bumped after a successful save, not before, so
        ordinary creates do not burn numbers."""
        seqs = [int(self._ticket(f"t{i}").ticket_number.rpartition("-")[2]) for i in range(4)]
        self.assertEqual(seqs, list(range(seqs[0], seqs[0] + 4)))

    def test_it_does_not_collide_with_the_invoice_sequences(self):
        """Same table, different prefixes."""
        self._ticket()
        self.assertEqual(
            set(IssuedNumberHighWater.objects.values_list("prefix", flat=True)), {"TCK"}
        )

    def test_an_explicitly_numbered_ticket_is_left_alone(self):
        ticket = Ticket(customer=self.customer, subject="imported", description="x",
                        ticket_number="TCK-000999")
        ticket.save()
        self.assertEqual(ticket.ticket_number, "TCK-000999")

    def test_an_unparseable_legacy_number_does_not_break_the_sequence(self):
        legacy = Ticket(customer=self.customer, subject="legacy", description="x",
                        ticket_number="OLD-REF-7")
        legacy.save()
        self.assertTrue(self._ticket().ticket_number.startswith("TCK-"))


class TicketNumberRaceTests(TransactionTestCase):
    """The retry loop, exercised by taking the number out from under a
    save the way a concurrent create would."""

    def setUp(self):
        self.customer = Customer.objects.create(full_name="Race Rina", email="r@example.com")

    def test_a_clash_is_retried_rather_than_500ing(self):
        from unittest import mock

        Ticket.objects.create(customer=self.customer, subject="first", description="x")

        real = Ticket._next_ticket_number
        calls = []

        def _steal_the_number(self):
            number = real(self)
            calls.append(number)
            if len(calls) == 1:
                # Somebody else takes it between our computing it and our
                # INSERT -- exactly the race the loop exists for.
                Ticket.objects.create(
                    customer=Customer.objects.get(pk=1) if False else self.customer,
                    subject="thief", description="x", ticket_number=number,
                )
            return number

        with mock.patch.object(Ticket, "_next_ticket_number", _steal_the_number):
            ticket = Ticket.objects.create(
                customer=self.customer, subject="survivor", description="x"
            )
        self.assertTrue(len(calls) >= 2, "the clash was not retried")
        self.assertTrue(ticket.ticket_number.startswith("TCK-"))
        self.assertEqual(Ticket.objects.filter(ticket_number=ticket.ticket_number).count(), 1)
