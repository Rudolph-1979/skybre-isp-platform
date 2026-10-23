"""Month-to-date usage, arriving in the client's app once a day.

WHAT THESE TESTS ARE REALLY ABOUT

A number a customer will compare against their own expectations, sent on a
schedule rather than in response to anything they did. Four ways to get it
wrong, and a test for each:

  * the WRONG MONTH -- August's bytes under a September label, which is
    what happens the moment the period key and the bucket window are
    derived separately;
  * a FIGURE NOBODY ASKED FOR -- a zero pushed for a customer with no
    measured traffic reads as "you used nothing", not as "we have nothing
    to show you";
  * the SAME FIGURE, NIGHTLY -- a customer who disconnected on the 9th has
    an unchanging September total, and re-sending it every night is three
    weeks of notifications about one number;
  * a FIGURE RECORDED AS SENT THAT NEVER ARRIVED -- if the digest is
    written when the call failed, the next run skips it and the customer's
    app is wrong until the month rolls over.

Plus the one that costs money rather than trust: dry run must send
nothing at all, because Conecto have not confirmed this payload yet.

Nothing here makes an HTTP request.
"""
import datetime
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from billing.models import Service, Tariff
from customers.models import Customer
from radiusauth import usage

from .models import ConectoUsagePush

TOKEN = "test-shared-secret"
CONFIGURED = override_settings(CONECTO_API_KEY=TOKEN, CONECTO_WEBHOOK_TOKEN=TOKEN)

MB = 1024 * 1024
GB = 1024 ** 3


def at(y, m, d, h=0):
    return timezone.make_aware(
        datetime.datetime(y, m, d, h), timezone.get_current_timezone()
    )


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = ""
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._payload


def run(**flags):
    """The command, with its output captured."""
    out, err = StringIO(), StringIO()
    args = []
    for key, value in flags.items():
        flag = "--" + key.replace("_", "-")
        if value is True:
            args.append(flag)
        elif value is not None:
            args.extend([flag, str(value)])
    call_command("conecto_push_usage", *args, stdout=out, stderr=err)
    return out.getvalue() + err.getvalue()


@CONFIGURED
class UsagePushTests(TestCase):
    def setUp(self):
        self.tariff = Tariff.objects.create(
            name="Capped 100GB", price=Decimal("499"), data_cap_gb=100,
        )
        self.uncapped = Tariff.objects.create(name="Uncapped", price=Decimal("899"))

        self.customer = Customer.objects.create(
            full_name="Lucille Slabbert", customer_id="CUS-009900", city="Kroonstad",
        )
        Service.objects.create(
            customer=self.customer, tariff=self.tariff, status=Service.Status.ACTIVE,
            start_date="2026-08-01", radius_username="lucille",
        )
        # 5 GB down, 1 GB up, inside September 2026.
        usage.bank_usage("lucille", at(2026, 9, 4, 9), download=5 * GB, upload=1 * GB)

    # --- the payload -----------------------------------------------------

    @patch("conecto.client.requests.request")
    def test_a_months_usage_is_pushed(self, request):
        request.return_value = FakeResponse()

        run(period="2026-09", apply=True)

        self.assertEqual(request.call_count, 1)
        body = request.call_args.kwargs["json"]
        self.assertEqual(body["client_code"], "CUS-009900")
        self.assertEqual(body["period"], "2026-09")
        self.assertEqual(body["download_bytes"], 5 * GB)
        self.assertEqual(body["upload_bytes"], 1 * GB)
        self.assertEqual(body["total_bytes"], 6 * GB)
        self.assertEqual(body["cap_bytes"], 100 * GB)
        self.assertTrue(body["as_of"])

    @patch("conecto.client.requests.request")
    def test_another_months_bytes_are_not_counted(self, request):
        """The one that makes the period key mean anything. August traffic
        under a September label is invisible until a customer adds it up."""
        request.return_value = FakeResponse()
        usage.bank_usage("lucille", at(2026, 8, 30, 9), download=90 * GB, upload=0)

        run(period="2026-09", apply=True)

        self.assertEqual(request.call_args.kwargs["json"]["download_bytes"], 5 * GB)

    @patch("conecto.client.requests.request")
    def test_an_uncapped_service_sends_a_null_cap(self, request):
        """Not zero. A cap of zero drawn as a progress bar is a full bar."""
        request.return_value = FakeResponse()
        Service.objects.filter(customer=self.customer).update(tariff=self.uncapped)

        run(period="2026-09", apply=True)

        self.assertIsNone(request.call_args.kwargs["json"]["cap_bytes"])

    @patch("conecto.client.requests.request")
    def test_several_services_are_one_figure(self, request):
        request.return_value = FakeResponse()
        Service.objects.create(
            customer=self.customer, tariff=self.tariff, status=Service.Status.ACTIVE,
            start_date="2026-08-01", radius_username="lucille-backup",
        )
        usage.bank_usage("lucille-backup", at(2026, 9, 5, 9), download=2 * GB, upload=0)

        run(period="2026-09", apply=True)

        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.kwargs["json"]["download_bytes"], 7 * GB)

    # --- who is and is not pushed ----------------------------------------

    @patch("conecto.client.requests.request")
    def test_a_customer_with_no_traffic_is_not_pushed(self, request):
        """A zero is not "nothing to show". Nobody wants a notification
        telling them they used no internet."""
        request.return_value = FakeResponse()
        quiet = Customer.objects.create(full_name="Quiet Quinn", customer_id="CUS-009901")
        Service.objects.create(
            customer=quiet, tariff=self.tariff, status=Service.Status.ACTIVE,
            start_date="2026-08-01", radius_username="quinn",
        )

        run(period="2026-09", apply=True)

        codes = [c.kwargs["json"]["client_code"] for c in request.call_args_list]
        self.assertEqual(codes, ["CUS-009900"])

    @patch("conecto.client.requests.request")
    def test_a_customer_with_no_client_code_is_not_pushed(self, request):
        """Conecto key everything on the client code; there is nothing for
        the figure to attach to without one."""
        request.return_value = FakeResponse()
        nameless = Customer.objects.create(full_name="No Code")
        Customer.objects.filter(pk=nameless.pk).update(customer_id="")
        Service.objects.create(
            customer=nameless, tariff=self.tariff, status=Service.Status.ACTIVE,
            start_date="2026-08-01", radius_username="nocode",
        )
        usage.bank_usage("nocode", at(2026, 9, 6, 9), download=3 * GB, upload=0)

        run(period="2026-09", apply=True)

        codes = [c.kwargs["json"]["client_code"] for c in request.call_args_list]
        self.assertEqual(codes, ["CUS-009900"])

    @patch("conecto.client.requests.request")
    def test_one_customer_can_be_pushed_alone(self, request):
        request.return_value = FakeResponse()
        other = Customer.objects.create(full_name="Other Otto", customer_id="CUS-009902")
        Service.objects.create(
            customer=other, tariff=self.tariff, status=Service.Status.ACTIVE,
            start_date="2026-08-01", radius_username="otto",
        )
        usage.bank_usage("otto", at(2026, 9, 7, 9), download=1 * GB, upload=0)

        run(period="2026-09", client_code="CUS-009902", apply=True)

        codes = [c.kwargs["json"]["client_code"] for c in request.call_args_list]
        self.assertEqual(codes, ["CUS-009902"])

    # --- not saying the same thing every night ----------------------------

    @patch("conecto.client.requests.request")
    def test_an_unchanged_figure_is_not_pushed_again(self, request):
        """A customer who disconnected on the 9th has the same September
        total for three weeks."""
        request.return_value = FakeResponse()

        run(period="2026-09", apply=True)
        run(period="2026-09", apply=True)

        self.assertEqual(request.call_count, 1)

    @patch("conecto.client.requests.request")
    def test_more_traffic_is_pushed(self, request):
        request.return_value = FakeResponse()
        run(period="2026-09", apply=True)

        usage.bank_usage("lucille", at(2026, 9, 11, 9), download=2 * GB, upload=0)
        run(period="2026-09", apply=True)

        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args.kwargs["json"]["download_bytes"], 7 * GB)

    @patch("conecto.client.requests.request")
    def test_resend_overrides_the_skip(self, request):
        request.return_value = FakeResponse()
        run(period="2026-09", apply=True)

        run(period="2026-09", resend=True, apply=True)

        self.assertEqual(request.call_count, 2)

    @patch("conecto.client.requests.request")
    def test_a_failed_push_is_not_recorded_as_sent(self, request):
        """THE IMPORTANT ONE. Recording the digest on a failure means the
        next run skips it, and the customer's app stays wrong until the
        month rolls over."""
        request.return_value = FakeResponse(status_code=500)

        run(period="2026-09", apply=True)

        self.assertFalse(ConectoUsagePush.objects.exists())

        request.return_value = FakeResponse()
        run(period="2026-09", apply=True)

        self.assertEqual(request.call_count, 2)
        self.assertEqual(ConectoUsagePush.objects.get().download_bytes, 5 * GB)

    # --- the dry run ------------------------------------------------------

    @patch("conecto.client.requests.request")
    def test_a_dry_run_sends_nothing_and_records_nothing(self, request):
        """Conecto have not confirmed this payload shape. Until they do,
        the default has to be that nothing leaves."""
        output = run(period="2026-09")

        request.assert_not_called()
        self.assertFalse(ConectoUsagePush.objects.exists())
        self.assertIn("CUS-009900", output)
        self.assertIn("would push 1", output)

    @patch("conecto.client.requests.request")
    def test_the_period_defaults_to_this_calendar_month(self, request):
        request.return_value = FakeResponse()
        now = timezone.localtime()
        usage.bank_usage("lucille", now, download=4 * GB, upload=0)

        run(apply=True)

        self.assertEqual(
            request.call_args.kwargs["json"]["period"], now.strftime("%Y-%m")
        )

    def test_a_malformed_period_is_refused(self):
        self.assertIn("2026-09", run(period="September"))
