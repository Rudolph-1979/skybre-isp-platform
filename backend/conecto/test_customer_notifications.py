"""Financial notifications mirrored to the client's Conecto app (12 Oct 2026).

Payment reminders, suspension warnings (and "today"), suspended, payment
received and service restored go to Conecto's customerNotification whatever
happened to the email. Invoices and statements stay on invoiceCreated /
statementCreated. Off unless CONECTO_NOTIFICATIONS_ENABLED.
"""
import datetime
from decimal import Decimal
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from billing.models import CustomerBillingConfig, Invoice, Service, Tariff
from billing.reactivation import reconnect_customer
from conecto.models import ConectoCall
from customers.models import Customer
from notifications.models import EmailLog
from notifications.services import send_customer_email

CONFIGURED = override_settings(
    CONECTO_API_KEY="test-key", CONECTO_WEBHOOK_TOKEN="test-key",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)


class Ok:
    status_code = 200
    ok = True
    text = ""

    def json(self):
        return {"success": True}


@CONFIGURED
class CustomerNotificationTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(full_name="Lefu Motaung", email="lefu@example.com",
                                                customer_id="SWF2550", balance=Decimal("450.00"))
        self.invoice = Invoice.objects.create(customer=self.customer, status=Invoice.Status.UNPAID,
                                              date_due=datetime.date(2026, 10, 1), total=Decimal("450.00"))
        patcher = mock.patch("conecto.client.notifications_enabled", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _sent(self, fn):
        with mock.patch("conecto.client.requests.request", return_value=Ok()) as post:
            fn()
        return [c.kwargs.get("json") for c in post.call_args_list if c.args[1].endswith("/customerNotification")]

    def test_payment_reminder_goes_to_conecto(self):
        payloads = self._sent(lambda: send_customer_email("payment_reminder", self.customer, invoice=self.invoice))
        self.assertEqual(len(payloads), 1)
        p = payloads[0]
        log = EmailLog.objects.get(template_key="payment_reminder")
        self.assertEqual(p["notification_id"], f"vexar-email-{log.pk}")
        self.assertEqual((p["client_code"], p["type"], p["balance"], p["invoice_number"]),
                         ("SWF2550", "payment_reminder", "450.00", self.invoice.number))
        self.assertEqual(p["due_date"], "2026-10-01")
        self.assertEqual(p["email_status"], "sent")
        self.assertEqual(p["title"], "Payment reminder")
        self.assertTrue(ConectoCall.objects.filter(endpoint="customerNotification", status="ok").exists())

    def test_warning_countdown_and_today(self):
        when = datetime.date(2026, 10, 14)
        p = self._sent(lambda: send_customer_email(
            "suspension_warning", self.customer, extra_context={"suspend_date": when, "days_until_suspension": 2}))[0]
        self.assertEqual((p["type"], p["suspend_date"], p["days_until_suspension"]), ("suspension_warning", "2026-10-14", 2))
        self.assertIn("14/10/2026", p["message"])
        p = self._sent(lambda: send_customer_email(
            "suspension_warning", self.customer, extra_context={"suspend_date": when, "days_until_suspension": 0}))[0]
        self.assertEqual(p["type"], "suspension_today")

    def test_sent_even_with_no_email_address(self):
        Customer.objects.filter(pk=self.customer.pk).update(email="")
        self.customer.refresh_from_db()
        p = self._sent(lambda: send_customer_email("suspension", self.customer))[0]
        self.assertEqual((p["type"], p["email_status"]), ("service_suspended", "no_email"))

    def test_invoices_and_statements_are_not_mirrored(self):
        self.assertEqual(self._sent(lambda: send_customer_email("invoice", self.customer, invoice=self.invoice)), [])

    def test_switched_off_sends_nothing(self):
        with mock.patch("conecto.client.notifications_enabled", return_value=False):
            self.assertEqual(self._sent(lambda: send_customer_email("payment_reminder", self.customer)), [])

    def test_resync_leaves_stale_notifications_alone(self):
        old = ConectoCall.objects.create(endpoint="customerNotification", payload={"x": 1}, status="failed")
        ConectoCall.objects.filter(pk=old.pk).update(created_at=timezone.now() - datetime.timedelta(days=3))
        with mock.patch("conecto.client.requests.request", return_value=Ok()) as post:
            call_command("conecto_resync", "--apply", stdout=mock.MagicMock())
        self.assertEqual(post.call_count, 0)


@CONFIGURED
class ServiceRestoredTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(full_name="Thami Guzana", email="thami@example.com",
                                                customer_id="SR2547", balance=Decimal("0.00"),
                                                status=Customer.Status.ACTIVE)
        tariff = Tariff.objects.create(name="W 4", price=Decimal("149.00"), tax_rate_pct=Decimal("15"))
        for _ in range(2):
            Service.objects.create(customer=self.customer, tariff=tariff, status=Service.Status.SUSPENDED,
                                   suspended_for_nonpayment=True)
        config = CustomerBillingConfig.for_customer(self.customer)
        config.billing_enabled = True
        config.blocking_period_days = 4
        config.minimum_balance = Decimal("50.00")
        config.save()

    def test_one_service_restored_notice_however_many_lines(self):
        with mock.patch("conecto.client.notifications_enabled", return_value=True), \
                mock.patch("conecto.client.requests.request", return_value=Ok()) as post:
            done = reconnect_customer(self.customer.pk)
        self.assertEqual(len(done), 2)
        self.assertEqual(EmailLog.objects.filter(template_key="service_restored").count(), 1)
        pushed = [c.kwargs["json"] for c in post.call_args_list if c.args[1].endswith("/customerNotification")]
        self.assertEqual([p["type"] for p in pushed], ["service_restored"])

    def test_respects_send_billing_notifications(self):
        CustomerBillingConfig.objects.filter(customer=self.customer).update(send_billing_notifications=False)
        reconnect_customer(self.customer.pk)
        self.assertFalse(EmailLog.objects.filter(template_key="service_restored").exists())
