"""Send / resend an invoice, quote or pro forma (Rudolph, 7 Oct 2026:
"Nowhere is there an option to send or resend invoices")."""
import datetime
from decimal import Decimal
from unittest.mock import patch

from django.core import mail
from django.core.mail import get_connection
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from billing.models import Invoice, InvoiceItem
from customers.models import Customer
from notifications.models import EmailLog


class InvoiceSendTests(TestCase):
    def setUp(self):
        patcher = patch("notifications.services.get_email_connection",
                        side_effect=lambda: get_connection("django.core.mail.backends.locmem.EmailBackend"))
        patcher.start()
        self.addCleanup(patcher.stop)
        admin = User.objects.create_user(username="boss", password="x", role="admin")
        self.api = APIClient()
        self.api.force_authenticate(admin)
        self.customer = Customer.objects.create(full_name="Itumeleng K", customer_id="SWF3001",
                                                email="itu@example.com", billing_email="accounts@example.com")

    def doc(self, status):
        inv = Invoice.objects.create(customer=self.customer, status=status, date_due=datetime.date(2026, 10, 7))
        InvoiceItem.objects.create(invoice=inv, description="Skybre Wireless 5 Mbps", quantity=1,
                                   unit_price=Decimal("249.00"), tax_rate_pct=Decimal("15"))
        return inv

    def test_send_and_resend(self):
        inv = self.doc(Invoice.Status.UNPAID)
        self.assertIsNone(self.api.get(f"/api/invoices/{inv.pk}/").data["last_emailed_at"])
        res = self.api.post(f"/api/invoices/{inv.pk}/send/", {}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual((res.data["sent_to"], res.data["cc"]), ("accounts@example.com", "itu@example.com"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertTrue(mail.outbox[0].attachments[0][0].endswith(".pdf"))
        log = EmailLog.objects.get()
        self.assertEqual((log.invoice, log.template_key), (inv, "invoice"))
        detail = self.api.get(f"/api/invoices/{inv.pk}/").data
        self.assertEqual(detail["last_emailed_to"], "accounts@example.com")
        self.assertIsNotNone(detail["last_emailed_at"])
        listed = self.api.get(f"/api/invoices/?customer={self.customer.pk}").data["results"][0]
        self.assertIsNotNone(listed["last_emailed_at"])
        self.assertEqual(self.api.post(f"/api/invoices/{inv.pk}/send/", {}, format="json").status_code, 200)
        self.assertEqual(len(mail.outbox), 2)

    def test_quote_uses_quote_email_and_draft_refused(self):
        quote = self.doc(Invoice.Status.QUOTE)
        self.api.post(f"/api/invoices/{quote.pk}/send/", {}, format="json")
        self.assertEqual(EmailLog.objects.get().template_key, "quote")
        draft = self.doc(Invoice.Status.DRAFT)
        self.assertEqual(self.api.post(f"/api/invoices/{draft.pk}/send/", {}, format="json").status_code, 400)

    def test_no_email_address(self):
        Customer.objects.filter(pk=self.customer.pk).update(email="", billing_email="")
        inv = self.doc(Invoice.Status.UNPAID)
        res = self.api.post(f"/api/invoices/{inv.pk}/send/", {}, format="json")
        self.assertEqual(res.status_code, 502)
        self.assertIn("no email", res.data["detail"])
