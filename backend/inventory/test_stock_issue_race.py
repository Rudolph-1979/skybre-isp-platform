"""Issuing stock the store does not actually have.

Availability was decided by reading quantity_on_hand -- a SUM over
StockMovement -- and then writing a negative movement, with nothing held
in between. An aggregate takes no locks and cannot see another
transaction's uncommitted issue, so two technicians drawing from the same
bin at the same moment both read the same figure, both passed the check,
and both wrote their line. Stock on hand went negative and a job was
dispatched with kit nobody had.
"""
import threading
from unittest import mock

from django.db import connection
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from accounts.models import User
from inventory.models import Product, StockMovement


class StockIssueRaceTests(TransactionTestCase):
    """Real threads and real commits -- the interleaving only exists
    across separate transactions, so TestCase's wrapping transaction
    cannot show it."""

    def setUp(self):
        self.staff = User.objects.create_user(
            username="stores", password="x", role=User.Role.ADMIN
        )
        self.product = Product.objects.create(
            name="RJ45 connectors", tracking_type=Product.TrackingType.QUANTITY
        )
        StockMovement.objects.create(
            product=self.product,
            movement_type=StockMovement.MovementType.RECEIPT,
            quantity=5,
        )

    def test_two_technicians_cannot_both_draw_the_last_of_the_stock(self):
        """Two issues raised in the same instant, each for 3 of the 5 boxes
        on the shelf. One has to be refused: the alternative is stock on
        hand of -1 and a technician sent to a job with connectors that are
        not in the store."""
        real_count = Product.quantity_on_hand.fget
        # Both requests are held here until each has read stock on hand --
        # the exact interleaving the bug needs. Once the product row is
        # locked the second request cannot reach this point at all, so the
        # first times out of the barrier and carries on alone.
        barrier = threading.Barrier(2, timeout=3)

        def _read_then_wait_for_the_other(product):
            value = real_count(product)
            try:
                barrier.wait()
            except threading.BrokenBarrierError:
                pass
            return value

        results = {}

        def _issue(index):
            client = APIClient()
            client.force_authenticate(self.staff)
            try:
                res = client.post(
                    "/api/stock-issues/",
                    {"lines": [{"product": self.product.pk, "quantity": 3}]},
                    format="json",
                )
                results[index] = res.status_code
            finally:
                connection.close()

        threads = [threading.Thread(target=_issue, args=(i,)) for i in range(2)]
        with mock.patch.object(
            Product, "quantity_on_hand", property(_read_then_wait_for_the_other)
        ):
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)

        self.assertEqual(
            sorted(results.values()),
            [201, 400],
            "both issues were accepted -- the second was never refused",
        )
        self.assertEqual(
            self.product.quantity_on_hand,
            2,
            "stock on hand no longer matches what is on the shelf",
        )
    def test_the_product_row_is_locked_before_stock_on_hand_is_counted(self):
        """Deterministic companion to the test above. Whatever the timing,
        the SUM that decides whether there is enough stock must be taken
        while the product row is held -- if it is read first, two requests
        can still read the same figure."""
        client = APIClient()
        client.force_authenticate(self.staff)
        with CaptureQueriesContext(connection) as captured:
            res = client.post(
                "/api/stock-issues/",
                {"lines": [{"product": self.product.pk, "quantity": 1}]},
                format="json",
            )
        self.assertEqual(res.status_code, 201, res.data)

        statements = [q["sql"] for q in captured.captured_queries]
        locks = [
            i for i, sql in enumerate(statements)
            if "inventory_product" in sql and "FOR UPDATE" in sql.upper()
        ]
        counts = [
            i for i, sql in enumerate(statements)
            if "inventory_stockmovement" in sql and "SUM(" in sql.upper()
        ]
        self.assertTrue(locks, "the product row was never locked")
        self.assertTrue(counts, "stock on hand was never counted")
        self.assertLess(
            locks[0], counts[0], "stock was counted before the row was locked"
        )
