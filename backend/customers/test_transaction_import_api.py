"""The two new endpoints wiring billing.transaction_import into
CustomerViewSet -- import-transactions-preview / import-transactions-commit,
alongside the existing import-ledger-preview / import-ledger-commit they
were built to mirror. billing.test_transaction_import already covers the
import logic itself in depth; this covers only what the view layer adds:
routing, permissions, and that an uploaded file actually reaches the
module and its response reaches the caller.

A NOTE ON PERMISSIONS: both @action decorators here (and their
import-ledger-* siblings) declare `permission_classes=[..., IsManagement]`,
which reads as "Management or Admin only". It isn't, in practice --
CustomerViewSet.get_permissions() is overridden and branches on
self.action itself; every action other than retrieve/list/ids/picker
falls through to its own catch-all
`[IsAuthenticated(), IsStaffMember(), HasCustomersAccess()]`, which
overrides whatever the action's own decorator says. So what actually
gates these four endpoints today is "staff with the Customers section",
not Management -- confirmed empirically below (a Management-role user
with no allowed_sections is refused; an Admin, who always has every
section, is not). Worth Rudolph knowing about since it's a real gap
between what the code says and what it does, but it's pre-existing on
the ledger-import actions too and not something this feature introduces
or is the place to silently change -- so these tests assert what the
endpoint actually does, not what its decorator claims.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from customers.models import Customer

User = get_user_model()

HEADER = "#,ID,Transaction date,Debit,Credit,Account balance,Description,Category,Type\n"
ONE_INVOICE_CSV = (
    HEADER
    + "1,82027,01/07/2026,R918.85,,R934.72,Skybre Wireless 10Mbps,SW 10/10,Debit\n"
    + "2,82028,01/07/2026,,R171.35,R763.37,Discount for Skybre Wireless 10Mbps,SW 10/10,Credit\n"
    + "3,82029,01/07/2026,R17.25,,R780.62,Public IP Rental,Service,Debit\n"
)


def _upload(text=ONE_INVOICE_CSV):
    return SimpleUploadedFile("transactions.csv", text.encode("utf-8"), content_type="text/csv")


class TransactionImportEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.customer = Customer.objects.create(
            full_name="Jayden Van Wyk", email="jayden@example.com", balance=Decimal("0.00"),
        )
        # Admin always has every section (see user_can_access_section) --
        # the simplest way to reliably pass HasCustomersAccess, which is
        # what actually gates these actions (see the module docstring).
        self.management = User.objects.create_user(
            username="admin-txn", password="pw-for-tests", role=User.Role.ADMIN,
        )
        # A support user with no allowed_sections -- lacks the Customers
        # section, so refused regardless of what the action's own
        # decorator claims to require.
        self.support = User.objects.create_user(
            username="support-txn", password="pw-for-tests", role=User.Role.SUPPORT,
        )

    def _url(self, action):
        return f"/api/customers/{self.customer.id}/import-transactions-{action}/"

    def test_preview_refuses_staff_without_customers_section_access(self):
        self.client.force_authenticate(self.support)
        res = self.client.post(self._url("preview"), {"file": _upload()}, format="multipart")
        self.assertEqual(res.status_code, 403)

    def test_commit_refuses_staff_without_customers_section_access(self):
        self.client.force_authenticate(self.support)
        res = self.client.post(self._url("commit"), {"file": _upload()}, format="multipart")
        self.assertEqual(res.status_code, 403)

    def test_preview_writes_nothing(self):
        self.client.force_authenticate(self.management)
        res = self.client.post(self._url("preview"), {"file": _upload()}, format="multipart")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["summary"]["invoices"], 1)
        self.assertEqual(self.customer.invoices.count(), 0)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal("0.00"))

    def test_commit_writes_and_returns_the_written_counts(self):
        self.client.force_authenticate(self.management)
        res = self.client.post(self._url("commit"), {"file": _upload()}, format="multipart")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["written"], {"invoices": 1, "payments": 0, "credits": 0})
        self.assertEqual(self.customer.invoices.count(), 1)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal("764.75"))

    def test_commit_with_errors_returns_400_and_writes_nothing(self):
        """Row 1 alone can never fail the chain check -- there's no prior
        balance to check it against (see plan()'s own reasoning). The
        break has to land on a later row, so it's the second row's
        balance that's corrupted here, not the first."""
        self.client.force_authenticate(self.management)
        broken = (
            HEADER
            + "1,82027,01/07/2026,R918.85,,R934.72,Skybre Wireless 10Mbps,SW 10/10,Debit\n"
            + "2,82028,01/07/2026,,R171.35,R999.99,Discount for Skybre Wireless 10Mbps,SW 10/10,Credit\n"
        )
        res = self.client.post(self._url("commit"), {"file": _upload(broken)}, format="multipart")
        self.assertEqual(res.status_code, 400)
        self.assertTrue(res.data["errors"])
        self.assertEqual(self.customer.invoices.count(), 0)

    def test_missing_file_is_a_clean_400_not_a_500(self):
        self.client.force_authenticate(self.management)
        res = self.client.post(self._url("preview"), {}, format="multipart")
        self.assertEqual(res.status_code, 400)

    def test_ledger_import_endpoints_are_unaffected(self):
        """Sanity check that adding the new actions didn't disturb the
        existing ones they sit beside on the same viewset."""
        self.client.force_authenticate(self.management)
        ledger_csv = "Type,Number,Date,Total,Due,Status\nRecurring invoice,INV900100,01/09/2026,R764.75,,Paid\n"
        res = self.client.post(
            f"/api/customers/{self.customer.id}/import-ledger-preview/",
            {"file": _upload(ledger_csv)}, format="multipart",
        )
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["summary"]["invoices"], 1)
