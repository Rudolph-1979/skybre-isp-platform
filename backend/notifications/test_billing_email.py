"""Billing email (Rudolph, 1 Oct): money documents go to the client's
billing_email with the main email in CC; everything else stays on the
main email; blank billing_email = main email as before."""
from unittest import mock

from django.core import mail
from django.core.mail import get_connection
from django.test import TestCase

from customers.models import Customer

from .models import EmailLog, EmailTemplate
from .services import billing_recipients, send_customer_email


def _locmem():
    return get_connection("django.core.mail.backends.locmem.EmailBackend")


class BillingRecipientsTests(TestCase):
    def _c(self, email="main@x.com", billing=""):
        return Customer(full_name="A", email=email, billing_email=billing)

    def test_billing_document_goes_to_billing_with_main_cc(self):
        self.assertEqual(
            billing_recipients(EmailTemplate.Key.INVOICE, self._c(billing="acc@x.com")), ("acc@x.com", "main@x.com")
        )

    def test_every_money_template_counts(self):
        c = self._c(billing="acc@x.com")
        for key in ["quote", "proforma", "statement", "invoice", "payment_reminder",
                    "suspension_warning", "suspension", "payment_received"]:
            self.assertEqual(billing_recipients(key, c)[0], "acc@x.com", key)

    def test_welcome_stays_on_main(self):
        self.assertEqual(billing_recipients(EmailTemplate.Key.WELCOME, self._c(billing="acc@x.com")), ("main@x.com", ""))

    def test_no_billing_email_means_main_as_before(self):
        self.assertEqual(billing_recipients(EmailTemplate.Key.INVOICE, self._c()), ("main@x.com", ""))

    def test_same_address_is_not_copied_to_itself(self):
        self.assertEqual(
            billing_recipients(EmailTemplate.Key.INVOICE, self._c(billing="MAIN@x.com")), ("MAIN@x.com", "")
        )

    def test_only_a_billing_email(self):
        self.assertEqual(billing_recipients(EmailTemplate.Key.INVOICE, self._c(email="", billing="acc@x.com")), ("acc@x.com", ""))


@mock.patch("notifications.services._build_attachment", return_value=None)
@mock.patch("notifications.services.get_email_connection", side_effect=_locmem)
class SendCustomerEmailTests(TestCase):
    def setUp(self):
        for key in (EmailTemplate.Key.INVOICE, EmailTemplate.Key.WELCOME):
            EmailTemplate.objects.update_or_create(key=key, defaults={"name": key, "subject": "S", "body_html": "<p>x</p>"})
        self.customer = Customer.objects.create(full_name="Acme", email="owner@acme.co.za", billing_email="accounts@acme.co.za")

    def test_invoice_mail_is_addressed_and_logged_with_cc(self, _conn, _att):
        log = send_customer_email(EmailTemplate.Key.INVOICE, self.customer)
        self.assertEqual(log.status, EmailLog.Status.SENT, log.error_message)
        self.assertEqual(mail.outbox[-1].to, ["accounts@acme.co.za"])
        self.assertEqual(mail.outbox[-1].cc, ["owner@acme.co.za"])
        self.assertEqual((log.recipient_email, log.cc_email), ("accounts@acme.co.za", "owner@acme.co.za"))

    def test_welcome_mail_goes_to_main_only(self, _conn, _att):
        log = send_customer_email(EmailTemplate.Key.WELCOME, self.customer)
        self.assertEqual(log.status, EmailLog.Status.SENT, log.error_message)
        self.assertEqual(mail.outbox[-1].to, ["owner@acme.co.za"])
        self.assertEqual(mail.outbox[-1].cc, [])
        self.assertEqual(log.cc_email, "")
