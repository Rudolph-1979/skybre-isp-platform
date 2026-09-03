"""Reseller-restricted staff must not see other partners' finances.

ScopedByCustomerMixin scoped staff-vs-customer and stopped there, so a
staff member restricted to one reseller correctly saw only that
reseller's customers on the Customers page -- and then every partner's
invoices, payments and services on the Finance page, with names and
totals. Because get_object() shares the queryset, they could PATCH and
DELETE a competitor's invoices too.

Every other app in the project already applied the filter (customers,
sales, network, radiusauth, audit, bankfeeds), including
UpcomingBlocksView in the same file as the mixin, whose docstring says a
reseller-scoped staff member "must not learn the names of customers
outside their partners".
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from billing.models import CreditRequest, Invoice, InvoiceItem, Payment, Service, Tariff
from customers.models import Customer, Partner

User = get_user_model()


class BillingPartnerScopingTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.acme = Partner.objects.create(name="Acme Fibre")
        self.rival = Partner.objects.create(name="Rival Networks")
        self.tariff = Tariff.objects.create(
            name="Home 20", price=Decimal("500.00"),
            speed_download_kbps=20480, speed_upload_kbps=10240,
        )

        self.mine = Customer.objects.create(
            full_name="Acme Customer", email="ac@example.com", partner=self.acme
        )
        self.theirs = Customer.objects.create(
            full_name="Rival Customer", email="rc@example.com", partner=self.rival
        )
        self.direct = Customer.objects.create(
            full_name="Direct Customer", email="dc@example.com", partner=None
        )

        self.my_invoice = self._invoice(self.mine, "100.00")
        self.their_invoice = self._invoice(self.theirs, "900.00")
        self.direct_invoice = self._invoice(self.direct, "50.00")

        Payment.objects.create(customer=self.mine, amount=Decimal("10.00"))
        Payment.objects.create(customer=self.theirs, amount=Decimal("90.00"))
        Service.objects.create(
            customer=self.mine, tariff=self.tariff, status=Service.Status.ACTIVE,
            start_date="2026-01-01",
        )
        Service.objects.create(
            customer=self.theirs, tariff=self.tariff, status=Service.Status.ACTIVE,
            start_date="2026-01-01",
        )

        self.restricted = User.objects.create_user(
            username="acme-accounts", password="pw-for-tests",
            role=User.Role.ACCOUNTS, allowed_partners=[self.acme.id],
        )
        self.unrestricted = User.objects.create_user(
            username="hq-accounts", password="pw-for-tests", role=User.Role.ACCOUNTS
        )
        self.admin = User.objects.create_user(
            username="root", password="pw-for-tests",
            role=User.Role.ADMIN, allowed_partners=[self.acme.id],
        )

    def _invoice(self, customer, total):
        invoice = Invoice.objects.create(
            customer=customer, status=Invoice.Status.UNPAID, date_due="2026-09-30"
        )
        InvoiceItem.objects.create(
            invoice=invoice, description="Service", quantity=1,
            unit_price=Decimal(total), tax_rate_pct=Decimal("0.00"),
        )
        invoice.recalc_totals()
        return invoice

    def _ids(self, path, user):
        self.client.force_authenticate(user)
        res = self.client.get(path)
        self.assertEqual(res.status_code, 200, res.data)
        return {row["id"] for row in res.data["results"]}

    # ---- lists ---------------------------------------------------------

    def test_invoices_are_partner_scoped(self):
        ids = self._ids("/api/invoices/?page_size=100", self.restricted)
        self.assertIn(self.my_invoice.id, ids)
        self.assertNotIn(self.their_invoice.id, ids)

    def test_a_direct_customers_invoice_is_still_visible(self):
        """No-partner customers aren't owned by any reseller, exactly as on
        the customer list."""
        ids = self._ids("/api/invoices/?page_size=100", self.restricted)
        self.assertIn(self.direct_invoice.id, ids)

    def test_payments_are_partner_scoped(self):
        self.client.force_authenticate(self.restricted)
        res = self.client.get("/api/payments/?page_size=100")
        names = {row["customer_name"] for row in res.data["results"]}
        self.assertIn("Acme Customer", names)
        self.assertNotIn("Rival Customer", names)

    def test_services_are_partner_scoped(self):
        self.client.force_authenticate(self.restricted)
        res = self.client.get("/api/services/?page_size=100")
        names = {row["customer_name"] for row in res.data["results"]}
        self.assertIn("Acme Customer", names)
        self.assertNotIn("Rival Customer", names)

    def test_credit_requests_are_partner_scoped(self):
        CreditRequest.objects.create(
            customer=self.theirs, amount=Decimal("100.00"), reason="theirs",
            requested_by=self.unrestricted,
        )
        CreditRequest.objects.create(
            customer=self.mine, amount=Decimal("100.00"), reason="mine",
            requested_by=self.unrestricted,
        )
        self.client.force_authenticate(self.restricted)
        res = self.client.get("/api/credit-requests/?page_size=100")
        reasons = {row["reason"] for row in res.data["results"]}
        self.assertIn("mine", reasons)
        self.assertNotIn("theirs", reasons)

    # ---- objects -------------------------------------------------------

    def test_another_partners_invoice_cannot_be_read(self):
        self.client.force_authenticate(self.restricted)
        res = self.client.get(f"/api/invoices/{self.their_invoice.id}/")
        self.assertEqual(res.status_code, 404)

    def test_another_partners_invoice_cannot_be_edited(self):
        self.client.force_authenticate(self.restricted)
        res = self.client.patch(
            f"/api/invoices/{self.their_invoice.id}/", {"status": "cancelled"}, format="json"
        )
        self.assertEqual(res.status_code, 404)
        self.their_invoice.refresh_from_db()
        self.assertEqual(self.their_invoice.status, Invoice.Status.UNPAID)

    def test_another_partners_invoice_cannot_be_deleted(self):
        self.client.force_authenticate(self.restricted)
        res = self.client.delete(f"/api/invoices/{self.their_invoice.id}/")
        self.assertEqual(res.status_code, 404)
        self.assertTrue(Invoice.objects.filter(pk=self.their_invoice.pk).exists())

    def test_my_own_partners_invoice_is_still_editable(self):
        self.client.force_authenticate(self.restricted)
        res = self.client.patch(
            f"/api/invoices/{self.my_invoice.id}/", {"note": "chased"}, format="json"
        )
        self.assertEqual(res.status_code, 200, res.data)

    # ---- the unrestricted tiers ----------------------------------------

    def test_unrestricted_staff_see_every_partner(self):
        ids = self._ids("/api/invoices/?page_size=100", self.unrestricted)
        self.assertIn(self.my_invoice.id, ids)
        self.assertIn(self.their_invoice.id, ids)

    def test_admin_sees_everything_despite_allowed_partners(self):
        """Same carve-out the customer list has: Admin is never narrowed."""
        ids = self._ids("/api/invoices/?page_size=100", self.admin)
        self.assertIn(self.their_invoice.id, ids)

    def test_a_customer_still_sees_only_their_own_invoices(self):
        """The other half of the mixin must keep working."""
        login = User.objects.create_user(
            username="portal-user", password="pw-for-tests", role=User.Role.CUSTOMER
        )
        self.mine.user = login
        self.mine.save(update_fields=["user"])
        ids = self._ids("/api/invoices/?page_size=100", login)
        self.assertEqual(ids, {self.my_invoice.id})


class CreditApprovalTests(TestCase):
    """Approving a credit must credit it once.

    The old approve() read the request, checked Pending, wrote the balance
    and then wrote the decision -- four steps in autocommit with no row
    lock, so two clicks 50 ms apart both passed the check and both
    credited.
    """

    def setUp(self):
        self.client = APIClient()
        self.manager = User.objects.create_user(
            username="manager1", password="pw-for-tests", role=User.Role.MANAGEMENT
        )
        self.customer = Customer.objects.create(
            full_name="Credit Cathy", email="cathy@example.com", balance=Decimal("1000.00")
        )
        self.credit = CreditRequest.objects.create(
            customer=self.customer, amount=Decimal("200.00"), reason="outage",
            requested_by=self.manager,
        )

    def test_approving_credits_once(self):
        self.client.force_authenticate(self.manager)
        res = self.client.post(f"/api/credit-requests/{self.credit.id}/approve/")
        self.assertEqual(res.status_code, 200, res.data)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal("800.00"))

    def test_approving_twice_credits_once(self):
        self.client.force_authenticate(self.manager)
        self.client.post(f"/api/credit-requests/{self.credit.id}/approve/")
        res = self.client.post(f"/api/credit-requests/{self.credit.id}/approve/")
        self.assertEqual(res.status_code, 400)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal("800.00"))

    def test_a_concurrent_payment_is_not_lost(self):
        """The F() half: a read-modify-write here lost whichever of the
        credit and the payment was written first."""
        self.client.force_authenticate(self.manager)
        stale = CreditRequest.objects.get(pk=self.credit.pk)
        Customer.objects.filter(pk=self.customer.pk).update(balance=Decimal("700.00"))
        res = self.client.post(f"/api/credit-requests/{stale.id}/approve/")
        self.assertEqual(res.status_code, 200, res.data)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal("500.00"))
