"""The backfill command, which sends real invoices to a real phone.

Every assertion here is ultimately about restraint. The command exists to
push a customer's history into their app, and the failure mode is not that
it sends too little -- it is thirty notifications about invoices somebody
settled months ago, fired because a dry run was skipped or a default was
wrong.

Nothing here touches the network.
"""
import datetime
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from billing.models import Invoice
from customers.models import Customer

from .models import ConectoInvoice

TOKEN = "test-shared-secret"
CONFIGURED = override_settings(
    CONECTO_API_KEY=TOKEN, CONECTO_WEBHOOK_TOKEN=TOKEN,
    CONECTO_URLS={"invoiceCreated": "https://example.invalid/invoiceCreated"},
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
class BackfillTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(
            full_name="CCTV Central", customer_id="SMF463", city="Kroonstad",
        )
        self.due = timezone.localdate()

    def _invoice(self, status, days_ago=0, total="435.85"):
        # suppressed() so setting up history does not push it as a side
        # effect -- otherwise every test here would start with the backfill
        # already done.
        from conecto.signals import suppressed

        with suppressed():
            return Invoice.objects.create(
                customer=self.customer, status=status,
                date_due=self.due - datetime.timedelta(days=days_ago),
                total=Decimal(total),
            )

    def _run(self, *args):
        out = StringIO()
        call_command("conecto_backfill_invoices", *args, stdout=out, stderr=out)
        return out.getvalue()

    # --- restraint --------------------------------------------------------

    @patch("conecto.client.requests.request")
    def test_a_dry_run_sends_nothing(self, request):
        self._invoice(Invoice.Status.UNPAID)

        output = self._run("--client-code", "SMF463")

        request.assert_not_called()
        self.assertIn("Dry run", output)
        self.assertFalse(ConectoInvoice.objects.exists())

    @patch("conecto.client.requests.request")
    def test_paid_invoices_are_left_out_by_default(self, request):
        """A customer cannot act on a settled invoice, and each one is
        another notification."""
        self._invoice(Invoice.Status.PAID)

        output = self._run("--client-code", "SMF463")

        self.assertIn("Nothing matches", output)

    @patch("conecto.client.requests.request")
    def test_include_paid_opts_into_the_noisy_half(self, request):
        request.return_value = FakeResponse(payload={})
        self._invoice(Invoice.Status.PAID)

        self._run("--client-code", "SMF463", "--include-paid", "--apply")

        self.assertEqual(request.call_count, 1)

    # --- doing the work ---------------------------------------------------

    @patch("conecto.client.requests.request")
    def test_apply_sends_unpaid_and_overdue(self, request):
        request.return_value = FakeResponse(payload={})
        self._invoice(Invoice.Status.UNPAID)
        self._invoice(Invoice.Status.OVERDUE, days_ago=40)

        output = self._run("--client-code", "SMF463", "--apply")

        self.assertEqual(request.call_count, 2)
        self.assertEqual(ConectoInvoice.objects.count(), 2)
        self.assertIn("Sent 2 of 2", output)

    @patch("conecto.client.requests.request")
    def test_running_it_twice_does_not_send_twice(self, request):
        """The whole history arriving a second time is the same phone
        buzzing again for nothing."""
        request.return_value = FakeResponse(payload={})
        self._invoice(Invoice.Status.UNPAID)
        self._run("--client-code", "SMF463", "--apply")

        output = self._run("--client-code", "SMF463", "--apply")

        self.assertEqual(request.call_count, 1)
        self.assertIn("already sent:   1", output)

    @patch("conecto.client.requests.request")
    def test_since_narrows_the_window(self, request):
        self._invoice(Invoice.Status.UNPAID, days_ago=400)
        self._invoice(Invoice.Status.UNPAID, days_ago=5)

        cutoff = (self.due - datetime.timedelta(days=30)).isoformat()
        output = self._run("--client-code", "SMF463", "--since", cutoff)

        self.assertIn("would be sent:  1", output)

    @patch("conecto.client.requests.request")
    def test_limit_allows_a_small_first_run(self, request):
        for day in range(5):
            self._invoice(Invoice.Status.UNPAID, days_ago=day)

        output = self._run("--client-code", "SMF463", "--limit", "2")

        self.assertIn("would be sent:  2", output)

    # --- refusals ---------------------------------------------------------

    @patch("conecto.client.requests.request")
    def test_an_unknown_client_code_sends_nothing(self, request):
        output = self._run("--client-code", "NOPE999")

        self.assertIn("No customer", output)
        request.assert_not_called()

    @patch("conecto.client.requests.request")
    def test_it_refuses_without_a_target(self, request):
        """--all is spelled out rather than implied. The difference between
        one customer and every customer should not be a forgotten flag."""
        output = self._run()

        self.assertIn("--client-code", output)
        request.assert_not_called()


@CONFIGURED
class CreditNoteTests(TestCase):
    """Credit notes must never reach the app as invoices.

    Caught in a dry run against a live customer -- three credits among
    thirty documents, all status "paid", all with negative totals. The
    status filter had no opinion about them because "is this a bill" and
    "has this bill been settled" are different questions.
    """

    def setUp(self):
        self.customer = Customer.objects.create(
            full_name="CCTV Central", customer_id="SMF463", city="Kroonstad",
        )
        self.due = timezone.localdate()

    def _doc(self, number, total, status=Invoice.Status.PAID):
        from conecto.signals import suppressed

        with suppressed():
            invoice = Invoice.objects.create(
                customer=self.customer, status=status, date_due=self.due,
                total=Decimal(total),
            )
        # The number is issued by save(); force the one under test.
        Invoice.objects.filter(pk=invoice.pk).update(number=number)
        invoice.refresh_from_db()
        return invoice

    @patch("conecto.client.requests.request")
    def test_a_credit_note_is_not_pushed_by_the_signal(self, request):
        from conecto import client

        credit = self._doc("CN202501000105", "-529.25")

        self.assertIsNone(client.push_invoice(credit))
        request.assert_not_called()

    @patch("conecto.client.requests.request")
    def test_a_negative_total_alone_is_enough(self, request):
        """Belt: a renumbering must not defeat this."""
        from conecto import client

        odd = self._doc("INV202501999999", "-100.00")

        self.assertIsNone(client.push_invoice(odd))
        request.assert_not_called()

    @patch("conecto.client.requests.request")
    def test_a_cn_prefix_alone_is_enough(self, request):
        """Braces: a zero-value credit must not defeat it either."""
        from conecto import client

        odd = self._doc("CN202501000999", "0.00")

        self.assertIsNone(client.push_invoice(odd))
        request.assert_not_called()

    @patch("conecto.client.requests.request")
    def test_an_ordinary_paid_invoice_still_goes(self, request):
        """The guard must not swallow the thing it sits next to."""
        from conecto import client

        request.return_value = FakeResponse(payload={})
        ordinary = self._doc("INV202606004199", "435.85")

        self.assertIsNotNone(client.push_invoice(ordinary))
        self.assertEqual(request.call_count, 1)

    @patch("conecto.client.requests.request")
    def test_the_backfill_excludes_them_and_says_so(self, request):
        self._doc("CN202501000105", "-529.25")
        self._doc("INV202606004199", "435.85")

        out = StringIO()
        call_command(
            "conecto_backfill_invoices", "--client-code", "SMF463",
            "--include-paid", stdout=out, stderr=out,
        )
        output = out.getvalue()

        self.assertIn("credit notes:   1 excluded", output)
        self.assertIn("would be sent:  1", output)
        self.assertNotIn("CN202501000105", output)
