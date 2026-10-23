"""Digital contract signing (Rudolph, 6 Oct 2026)."""
import base64
import hashlib
import io
from decimal import Decimal

from unittest.mock import patch

from django.core import mail
from django.core.mail import get_connection
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image, ImageDraw
from pypdf import PdfReader
from rest_framework.test import APIClient

from accounts.models import User
from billing.models import Service, Tariff
from customers.models import Customer
from notifications.models import EmailLog

from .models import Contract, ContractEvent, ContractSettings, ContractTemplate
from .services import ContractError, decode_signature


def signature(blank=False):
    image = Image.new("RGBA", (400, 150), (0, 0, 0, 0))
    if not blank:
        ImageDraw.Draw(image).line([(20, 100), (120, 30), (220, 110), (360, 40)], fill=(0, 0, 0, 255), width=6)
    out = io.BytesIO()
    image.save(out, format="PNG")
    return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode()


def locmem(test):
    patcher = patch("notifications.email_settings.get_email_connection",
                    side_effect=lambda: get_connection("django.core.mail.backends.locmem.EmailBackend"))
    patcher.start()
    test.addCleanup(patcher.stop)


def tiny_pdf():
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class ContractTests(TestCase):
    def setUp(self):
        locmem(self)
        self.staff = User.objects.create_user(username="janine", password="x", role="support", first_name="Janine",
                                              allowed_sections=["customers"])
        self.api = APIClient()
        self.api.force_authenticate(self.staff)
        self.customer = Customer.objects.create(full_name="Lucille Smit", customer_id="SWF2900",
                                                email="lucille@example.com", id_number="8001015009087",
                                                address="12 Reitz St", city="Kroonstad")
        tariff = Tariff.objects.create(name="20 Mbps", price=Decimal("500.00"), tax_rate_pct=Decimal("15"))
        Service.objects.create(customer=self.customer, tariff=tariff, status="active")
        self.template = ContractTemplate.objects.create(
            name="Standard", title="Internet Service Agreement",
            body_html="<p>{{ client_name }} ({{ client_code }}) on {{ plan }} at R {{ monthly_incl }}.</p>{{ services_table }}",
        )
        self.public = APIClient()

    def make(self, send=True):
        res = self.api.post("/api/contracts/", {"customer": self.customer.pk, "template": self.template.pk,
                                                "send": send}, format="json")
        self.assertEqual(res.status_code, 201, res.data)
        return Contract.objects.get(pk=res.data["id"])

    def test_template_is_filled_and_frozen(self):
        contract = self.make(send=False)
        self.assertIn("Lucille Smit (SWF2900) on 20 Mbps at R 575.00", contract.body_html)
        self.assertIn("12 Reitz St", contract.body_html)
        self.assertEqual(contract.status, "draft")
        self.assertEqual(hashlib.sha256(bytes(contract.document_pdf)).hexdigest(), contract.document_sha256)
        self.template.body_html = "<p>changed</p>"
        self.template.save()
        contract.refresh_from_db()
        self.assertIn("Lucille Smit", contract.body_html)

    def test_send_emails_the_link_and_logs_it(self):
        contract = self.make()
        self.assertEqual(contract.status, "sent")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(f"/sign/{contract.token}", mail.outbox[0].alternatives[0][0])
        self.assertEqual(mail.outbox[0].to, ["lucille@example.com"])
        self.assertTrue(EmailLog.objects.filter(customer=self.customer, template_key="contract", status="sent").exists())
        self.assertIsNotNone(contract.expires_at)

    def test_client_signs_online(self):
        contract = self.make()
        page = self.public.get(f"/api/public/contract/{contract.token}/")
        self.assertEqual(page.status_code, 200)
        self.assertEqual((page.data["state"], page.data["client_name"], page.data["in_person"]),
                         ("open", "Lucille Smit", False))
        self.assertIn("Lucille Smit", page.data["body_html"])
        contract.refresh_from_db()
        self.assertEqual(contract.status, "viewed")
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            res = self.public.post(f"/api/public/contract/{contract.token}/sign/",
                                   {"signer_name": " Lucille  Smit ", "signature": signature(), "agree": True,
                                    "id_number": "8001015009087"},
                                   format="json", HTTP_USER_AGENT="Mozilla/5.0 (Android)",
                                   HTTP_X_FORWARDED_FOR="41.13.1.2")
        self.assertEqual(res.status_code, 200, res.data)
        contract.refresh_from_db()
        self.assertEqual((contract.status, contract.signer_name, str(contract.signer_ip), contract.signed_in_person),
                         ("signed", "Lucille Smit", "41.13.1.2", False))
        signed = PdfReader(io.BytesIO(bytes(contract.signed_pdf)))
        original = PdfReader(io.BytesIO(bytes(contract.document_pdf)))
        self.assertEqual(len(signed.pages), len(original.pages) + 1, "certificate fits one page")
        certificate = signed.pages[-1].extract_text()
        self.assertIn("Signing certificate", certificate)
        self.assertIn("Lucille Smit", certificate)
        self.assertIn(contract.document_sha256[:16], certificate.replace("\n", ""))
        self.assertEqual(hashlib.sha256(bytes(contract.signed_pdf)).hexdigest(), contract.signed_sha256)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].attachments[0][2], "application/pdf")
        kinds = list(contract.events.values_list("kind", flat=True))
        self.assertEqual(kinds, ["created", "sent", "viewed", "signed"])
        again = self.public.post(f"/api/public/contract/{contract.token}/sign/",
                                 {"signer_name": "X Y Z", "signature": signature(), "agree": True}, format="json")
        self.assertEqual(again.status_code, 400)
        copy = self.public.get(f"/api/public/contract/{contract.token}/document/")
        self.assertEqual(copy.status_code, 200)
        self.assertEqual(copy.content, bytes(contract.signed_pdf))

    def test_refusals(self):
        contract = self.make()
        url = f"/api/public/contract/{contract.token}/sign/"
        for body in (
            {"signer_name": "Lucille Smit", "signature": signature(), "agree": False},
            {"signer_name": "Lu", "signature": signature(), "agree": True},
            {"signer_name": "Lucille Smit", "signature": signature(blank=True), "agree": True},
            {"signer_name": "Lucille Smit", "signature": "data:image/png;base64,AAAA", "agree": True},
            {"signer_name": "Lucille Smit", "signature": "javascript:alert(1)", "agree": True},
        ):
            self.assertEqual(self.public.post(url, body, format="json").status_code, 400, body)
        self.assertEqual(self.public.get("/api/public/contract/nope/").status_code, 404)
        Contract.objects.filter(pk=contract.pk).update(expires_at=timezone.now() - timezone.timedelta(minutes=1))
        self.assertEqual(self.public.get(f"/api/public/contract/{contract.token}/").data["state"], "expired")
        res = self.public.post(url, {"signer_name": "Lucille Smit", "signature": signature(), "agree": True},
                               format="json")
        self.assertIn("expired", res.data["detail"])

    def test_resend_after_expiry_reopens_the_link(self):
        contract = self.make()
        Contract.objects.filter(pk=contract.pk).update(expires_at=timezone.now() - timezone.timedelta(days=1))
        res = self.api.post(f"/api/contracts/{contract.pk}/send/", {}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        contract.refresh_from_db()
        self.assertFalse(contract.is_expired)

    def test_in_person_records_the_witness(self):
        contract = self.make(send=False)
        res = self.api.post(f"/api/contracts/{contract.pk}/in-person/", {}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        witness = res.data["url"].split("witness=")[1]
        page = self.public.get(f"/api/public/contract/{contract.token}/?witness={witness}")
        self.assertEqual((page.data["state"], page.data["in_person"], page.data["witness_name"]),
                         ("open", True, "Janine"))
        res = self.public.post(f"/api/public/contract/{contract.token}/sign/",
                               {"signer_name": "Lucille Smit", "signature": signature(), "agree": True,
                                "witness": witness}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        contract.refresh_from_db()
        self.assertTrue(contract.signed_in_person)
        self.assertEqual(contract.witnessed_by, self.staff)
        forged = Contract.objects.create(customer=self.customer, kind="upload", title="x", document_pdf=tiny_pdf(),
                                         document_sha256="0")
        self.assertEqual(self.public.get(f"/api/public/contract/{forged.token}/?witness={witness}").data["in_person"],
                         False)

    def test_upload(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        res = self.api.post("/api/contracts/", {
            "customer": self.customer.pk, "kind": "upload", "title": "Fibre addendum",
            "file": SimpleUploadedFile("addendum.pdf", tiny_pdf(), content_type="application/pdf"),
        }, format="multipart")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual((res.data["title"], res.data["kind"], res.data["status"]), ("Fibre addendum", "upload", "draft"))
        bad = self.api.post("/api/contracts/", {
            "customer": self.customer.pk, "kind": "upload",
            "file": SimpleUploadedFile("evil.pdf", b"<html>", content_type="application/pdf"),
        }, format="multipart")
        self.assertEqual(bad.status_code, 400)
        contract = Contract.objects.get(pk=res.data["id"])
        self.assertEqual(self.public.get(f"/api/public/contract/{contract.token}/").data["pages"], 1)
        self.assertEqual(self.public.get(f"/api/public/contract/{contract.token}/document/").status_code, 200)
        self.assertTrue(contract.events.filter(kind="document_opened").exists())

    def test_cancel_and_no_email(self):
        contract = self.make(send=False)
        self.assertEqual(self.api.post(f"/api/contracts/{contract.pk}/cancel/", {}, format="json").status_code, 200)
        self.assertEqual(self.public.get(f"/api/public/contract/{contract.token}/").data["state"], "cancelled")
        Customer.objects.filter(pk=self.customer.pk).update(email="")
        res = self.api.post("/api/contracts/", {"customer": self.customer.pk, "template": self.template.pk,
                                                "send": True}, format="json")
        self.assertEqual(res.status_code, 201)
        self.assertIn("no email", res.data["warning"])

    def test_staff_access(self):
        tech = User.objects.create_user(username="t", password="x", role="technician", allowed_sections=["scheduling"])
        api = APIClient()
        api.force_authenticate(tech)
        self.assertEqual(api.get(f"/api/contracts/?customer={self.customer.pk}").status_code, 403)
        self.assertEqual(APIClient().get("/api/contracts/").status_code, 401)
        contract = self.make(send=False)
        self.assertEqual(self.api.get(f"/api/contracts/{contract.pk}/document/").status_code, 200)
        listing = self.api.get(f"/api/contracts/?customer={self.customer.pk}")
        self.assertEqual([c["id"] for c in listing.data], [contract.pk])
        self.assertEqual(self.api.post(f"/api/contract-templates/", {"name": "x"}, format="json").status_code, 403)

    def test_template_preview_and_bad_syntax(self):
        admin = User.objects.create_user(username="boss", password="x", role="admin")
        api = APIClient()
        api.force_authenticate(admin)
        res = api.get(f"/api/contract-templates/{self.template.pk}/preview/?customer={self.customer.pk}")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Type"], "application/pdf")
        self.assertEqual(api.get(f"/api/contract-templates/{self.template.pk}/preview/").status_code, 200)
        bad = api.post("/api/contract-templates/", {"name": "Bad", "title": "T", "body_html": "{% if %}"}, format="json")
        self.assertEqual(bad.status_code, 400)

    def test_signature_is_reencoded(self):
        png = decode_signature(signature())
        self.assertTrue(png.startswith(b"\x89PNG"))
        with self.assertRaises(ContractError):
            decode_signature(signature(blank=True))


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class AutoSendTests(TestCase):
    def setUp(self):
        locmem(self)
        self.admin = User.objects.create_user(username="boss", password="x", role="admin", first_name="Rudolph")
        self.api = APIClient()
        self.api.force_authenticate(self.admin)
        self.template = ContractTemplate.objects.create(name="Std", title="Agreement", body_html="<p>{{ client_name }}</p>")

    def new_client(self, **extra):
        with self.captureOnCommitCallbacks(execute=True):
            res = self.api.post("/api/customers/", {"full_name": "Piet Pompies", "email": "piet@example.com",
                                                    **extra}, format="json")
        self.assertEqual(res.status_code, 201, res.data)
        return Customer.objects.get(pk=res.data["id"])

    def test_off_by_default(self):
        self.assertFalse(ContractSettings.load().auto_send_new_client)
        customer = self.new_client()
        self.assertFalse(customer.contracts.exists())

    def test_switching_on_needs_an_active_template(self):
        self.template.is_active = False
        self.template.save()
        res = self.api.patch("/api/contract-settings/", {"auto_send_new_client": True,
                                                         "default_template": self.template.pk}, format="json")
        self.assertEqual(res.status_code, 400)

    def test_new_client_gets_one(self):
        res = self.api.patch("/api/contract-settings/", {"auto_send_new_client": True,
                                                         "default_template": self.template.pk}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        customer = self.new_client()
        contract = customer.contracts.get()
        self.assertEqual((contract.status, contract.sent_to), ("sent", "piet@example.com"))
        self.assertEqual(len(mail.outbox), 1)

    def test_lead_accepted(self):
        from sales.models import Lead
        from sales.workflow import accept

        ContractSettings.objects.update_or_create(pk=1, defaults={"auto_send_lead_accepted": True,
                                                                  "default_template": self.template})
        tariff = Tariff.objects.create(name="10 Mbps", price=Decimal("400"), tax_rate_pct=Decimal("15"))
        lead = Lead.objects.create(full_name="Sarie Marais", email="sarie@example.com", interested_tariff=tariff,
                                   quote_items=[{"label": "Installation", "amount": "1500.00"}])
        with self.captureOnCommitCallbacks(execute=True):
            customer, _ = accept(lead, self.admin)
        contract = customer.contracts.get()
        self.assertEqual((contract.lead_id, contract.status), (lead.pk, "sent"))
        # A second acceptance path for the same client doesn't send another.
        from .services import auto_send

        self.assertIsNone(auto_send(customer.pk, "lead_accepted"))
        self.assertEqual(customer.contracts.count(), 1)

    def test_customer_filter(self):
        a = Customer.objects.create(full_name="A")
        b = Customer.objects.create(full_name="B")
        Customer.objects.create(full_name="C")
        Contract.objects.create(customer=a, kind="upload", title="x", document_pdf=b"x", document_sha256="0",
                                status="signed")
        Contract.objects.create(customer=b, kind="upload", title="x", document_pdf=b"x", document_sha256="0",
                                status="sent")

        def names(value):
            res = self.api.get(f"/api/customers/?contract={value}&page_size=50")
            return sorted(c["full_name"] for c in res.data["results"])

        self.assertEqual(names("signed"), ["A"])
        self.assertEqual(names("awaiting"), ["B"])
        self.assertEqual(names("none"), ["C"])
        self.assertEqual(ContractEvent.objects.count(), 0)
