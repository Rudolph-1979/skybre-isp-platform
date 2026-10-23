"""Mail must not be able to block forever.

get_email_connection built the SMTP backend with no `timeout`, so Django
passed None to smtplib.SMTP -- a blocking socket with no deadline. Mail
was the only outbound call in the platform without a limit; the RouterOS
API gets 8 seconds and CoA gets 4.

Where that bites is where nobody is watching. A recurring-billing run
sends one email per customer, serially, in-process: customers 1-299 get
their invoices, customer 300 blocks on a half-open connection to the
relay, and 301-1592 are never billed at all -- with the
RecurringBillingRun row left on PROCESSED and zeroed counts, because the
summary write at the end is never reached.
"""
from django.test import TestCase, override_settings

from notifications.email_settings import SMTP_TIMEOUT_SECONDS, get_email_connection
from notifications.models import EmailSettings


class SmtpTimeoutTests(TestCase):
    def _configure_smtp(self):
        settings_row = EmailSettings.load()
        settings_row.smtp_host = "mail.example.com"
        settings_row.smtp_port = 587
        settings_row.smtp_username = "billing@example.com"
        settings_row.use_tls = True
        settings_row.save()
        return settings_row

    def test_the_smtp_connection_carries_a_timeout(self):
        self._configure_smtp()
        connection = get_email_connection()
        self.assertEqual(connection.timeout, SMTP_TIMEOUT_SECONDS)

    def test_the_timeout_is_bounded_and_not_none(self):
        self._configure_smtp()
        connection = get_email_connection()
        self.assertIsNotNone(
            connection.timeout,
            "None means smtplib blocks with no deadline, which hangs a whole billing run",
        )
        self.assertLessEqual(connection.timeout, 60)
        self.assertGreaterEqual(connection.timeout, 5)

    def test_the_configured_host_and_port_still_come_through(self):
        """The timeout must not have displaced anything."""
        self._configure_smtp()
        connection = get_email_connection()
        self.assertEqual(connection.host, "mail.example.com")
        self.assertEqual(connection.port, 587)
        self.assertEqual(connection.username, "billing@example.com")
        self.assertTrue(connection.use_tls)

    def test_no_host_configured_still_falls_back_to_the_console(self):
        """A dev machine with nothing configured keeps behaving as before,
        rather than erroring on every email."""
        settings_row = EmailSettings.load()
        settings_row.smtp_host = ""
        settings_row.save()
        with override_settings(EMAIL_HOST=""):
            connection = get_email_connection()
        self.assertIn("console", type(connection).__module__)
