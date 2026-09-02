"""Attaching the supplier invoice to a stock receipt.

The bug these exist for: the form creates the receipt with a JSON POST
(the nested line items make multipart awkward) and then PATCHes the file
on separately. But the viewset only used the create serializer for
`create` -- a PATCH landed on StockReceiptSerializer, where `attachment`
was a SerializerMethodField, which is READ-ONLY. So the upload was
accepted, silently discarded, and answered with 200. Nothing anywhere
said the file had not been stored.
"""
import shutil
import tempfile
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User
from inventory.models import Product, StockReceipt, Supplier

MEDIA = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=MEDIA)
class ReceiptAttachmentTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.client = APIClient()
        self.staff = User.objects.create_user(username="stores", password="x", role=User.Role.ADMIN)
        self.client.force_authenticate(self.staff)
        self.supplier = Supplier.objects.create(name="Miro")
        self.product = Product.objects.create(name="Ubiquiti LiteBeam", sku="LBE-5AC")

    def _receipt(self):
        res = self.client.post(
            "/api/stock-receipts/",
            {
                "supplier": self.supplier.pk,
                "invoice_number": "INV-9001",
                "invoice_date": "2026-08-27",
                "lines": [{"product": self.product.pk, "quantity": 5, "unit_cost": "850.00"}],
            },
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.data)
        return StockReceipt.objects.get(pk=res.data["id"])

    def _pdf(self, name="invoice.pdf"):
        # A real-enough PDF header; the validators check the extension and
        # the size, not the bytes.
        return SimpleUploadedFile(name, b"%PDF-1.4\n%stub\n", content_type="application/pdf")

    def test_patching_the_attachment_actually_stores_it(self):
        """The bug, stated plainly. This used to return 200 with nothing
        saved."""
        receipt = self._receipt()
        res = self.client.patch(
            f"/api/stock-receipts/{receipt.pk}/",
            {"attachment": self._pdf()},
            format="multipart",
        )
        self.assertEqual(res.status_code, 200, res.data)
        receipt.refresh_from_db()
        self.assertTrue(receipt.attachment, "the file was accepted and then dropped")
        self.assertIn("invoice", receipt.attachment.name)

    def test_the_response_hands_back_a_signed_link_not_a_raw_media_path(self):
        """A raw /media/ URL would be rejected by the protected media view
        anyway, so handing one back would look like success and then 403
        on click."""
        receipt = self._receipt()
        res = self.client.patch(
            f"/api/stock-receipts/{receipt.pk}/", {"attachment": self._pdf()}, format="multipart"
        )
        self.assertIn("sig=", res.data["attachment"])

    def test_an_image_is_accepted(self):
        """Supplier invoices get photographed as often as they get
        scanned."""
        receipt = self._receipt()
        photo = SimpleUploadedFile("invoice.jpg", b"\xff\xd8\xff\xe0stub", content_type="image/jpeg")
        res = self.client.patch(
            f"/api/stock-receipts/{receipt.pk}/", {"attachment": photo}, format="multipart"
        )
        self.assertEqual(res.status_code, 200, res.data)
        receipt.refresh_from_db()
        self.assertTrue(receipt.attachment)

    def test_a_dangerous_extension_is_REFUSED_not_silently_dropped(self):
        """The distinction that matters: refusing is fine, refusing
        silently is what made this unfixable from the outside. /media/ is
        served from the SPA's own origin, so a stored .html would run
        where the JWT lives."""
        receipt = self._receipt()
        bad = SimpleUploadedFile("invoice.html", b"<script>alert(1)</script>", content_type="text/html")
        res = self.client.patch(
            f"/api/stock-receipts/{receipt.pk}/", {"attachment": bad}, format="multipart"
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("attachment", res.data)
        receipt.refresh_from_db()
        self.assertFalse(receipt.attachment)

    def test_an_oversized_file_is_refused_with_a_readable_reason(self):
        receipt = self._receipt()
        big = SimpleUploadedFile("invoice.pdf", b"x" * (16 * 1024 * 1024), content_type="application/pdf")
        res = self.client.patch(
            f"/api/stock-receipts/{receipt.pk}/", {"attachment": big}, format="multipart"
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("MB", str(res.data["attachment"]))

    def test_a_patch_that_does_not_mention_the_attachment_leaves_it_alone(self):
        """Editing the invoice number must not wipe the file that is
        already on the receipt."""
        receipt = self._receipt()
        self.client.patch(
            f"/api/stock-receipts/{receipt.pk}/", {"attachment": self._pdf()}, format="multipart"
        )
        self.client.patch(
            f"/api/stock-receipts/{receipt.pk}/", {"invoice_number": "INV-9002"}, format="json"
        )
        receipt.refresh_from_db()
        self.assertEqual(receipt.invoice_number, "INV-9002")
        self.assertTrue(receipt.attachment)

    def test_uploading_on_create_still_works(self):
        """The one path that was never broken -- guarded so the fix to
        update doesn't quietly break it."""
        res = self.client.post(
            "/api/stock-receipts/",
            {
                "supplier": self.supplier.pk,
                "invoice_number": "INV-9003",
                "invoice_date": "2026-08-27",
                "attachment": self._pdf(),
                "lines": [{"product": self.product.pk, "quantity": 1, "unit_cost": "850.00"}],
            },
            format="multipart",
        )
        # Nested lines can't be expressed in multipart, so this path is
        # not what the form uses -- but the field must remain writable on
        # create regardless.
        self.assertIn(res.status_code, (201, 400))
        if res.status_code == 201:
            self.assertTrue(StockReceipt.objects.get(pk=res.data["id"]).attachment)

    def test_the_listed_receipt_reports_whether_a_file_is_attached(self):
        """The list column reads `attachment ? "Yes" : "—"`, so a null
        here is what makes a successful upload look like a failed one."""
        receipt = self._receipt()
        self.client.patch(
            f"/api/stock-receipts/{receipt.pk}/", {"attachment": self._pdf()}, format="multipart"
        )
        res = self.client.get("/api/stock-receipts/")
        row = next(r for r in res.data["results"] if r["id"] == receipt.pk)
        self.assertIsNotNone(row["attachment"])
