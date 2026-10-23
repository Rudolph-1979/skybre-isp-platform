"""Allocating a payment across invoices.

The test that matters most is test_deleting_an_allocated_payment_puts_every
_invoice_back: allocation writes to Invoice.paid_amount, so if deletion
doesn't reverse it, a deleted payment leaves invoices reading as settled by
money that no longer exists — and bankfeeds puts the bank transaction back
in the review queue to be confirmed again, which is how one EFT gets
counted twice.
"""
import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from billing.allocation import (
    AllocationError, allocate, auto_allocate, open_invoices, unallocate,
    unallocated_amount,
)
from billing.models import (
    CustomerBillingConfig, Invoice, InvoiceItem, Payment, PaymentAllocation, Tariff,
)
from customers.models import Customer


class AllocationTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        User = get_user_model()
        self.staff = User.objects.create_user(
            username="accounts-clerk", password="x", role="accounts", email="clerk@example.com"
        )
        self.customer = Customer.objects.create(
            full_name="Hennenman Car Wash", customer_id="CUS-000003",
            email="wash@example.com", status=Customer.Status.ACTIVE,
        )
        # Keep the receipt email out of these tests.
        config = CustomerBillingConfig.objects.get_or_create(customer=self.customer)[0]
        config.send_billing_notifications = False
        config.save()
        self.tariff = Tariff.objects.create(
            name="Fibre 50/50", price=Decimal("539.00"), tax_rate_pct=Decimal("0"),
            speed_download_kbps=51200, speed_upload_kbps=51200,
        )

    def _invoice(self, amount, due_offset=0, status=Invoice.Status.UNPAID):
        invoice = Invoice.objects.create(
            customer=self.customer, status=status,
            date_due=self.today + datetime.timedelta(days=due_offset),
        )
        InvoiceItem.objects.create(
            invoice=invoice, item_type=InvoiceItem.ItemType.CUSTOM,
            description="Service", quantity=1, unit_price=Decimal(amount), tax_rate_pct=Decimal("0"),
        )
        invoice.recalc_totals()
        invoice.refresh_from_db()
        return invoice

    def _payment(self, amount):
        return Payment.objects.create(
            customer=self.customer, amount=Decimal(amount), method=Payment.Method.BANK_TRANSFER,
        )

    # ---- the service layer ----------------------------------------------

    def test_one_payment_settles_several_invoices(self):
        first = self._invoice("400.00")
        second = self._invoice("600.00")
        payment = self._payment("1000.00")

        allocate(payment, [(first.pk, Decimal("400.00")), (second.pk, Decimal("600.00"))], user=self.staff)

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.status, Invoice.Status.PAID)
        self.assertEqual(second.status, Invoice.Status.PAID)
        self.assertEqual(unallocated_amount(payment), Decimal("0"))

    def test_partial_allocation_leaves_the_invoice_unpaid(self):
        invoice = self._invoice("1000.00")
        payment = self._payment("400.00")

        allocate(payment, [(invoice.pk, Decimal("400.00"))])

        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.UNPAID)
        self.assertEqual(invoice.paid_amount, Decimal("400.00"))
        self.assertEqual(invoice.balance_due, Decimal("600.00"))

    def test_remainder_stays_unallocated_as_credit(self):
        invoice = self._invoice("300.00")
        payment = self._payment("500.00")

        allocate(payment, [(invoice.pk, Decimal("300.00"))])

        self.assertEqual(unallocated_amount(payment), Decimal("200.00"))

    def test_cannot_allocate_more_than_the_payment(self):
        invoice = self._invoice("1000.00")
        payment = self._payment("400.00")
        with self.assertRaises(AllocationError):
            allocate(payment, [(invoice.pk, Decimal("500.00"))])
        invoice.refresh_from_db()
        self.assertEqual(invoice.paid_amount, Decimal("0"))

    def test_cannot_allocate_more_than_the_invoice_owes(self):
        invoice = self._invoice("300.00")
        payment = self._payment("1000.00")
        with self.assertRaises(AllocationError):
            allocate(payment, [(invoice.pk, Decimal("400.00"))])
        self.assertEqual(PaymentAllocation.objects.count(), 0)

    def test_one_bad_line_rejects_the_whole_mapping(self):
        good = self._invoice("100.00")
        bad = self._invoice("100.00")
        payment = self._payment("1000.00")
        with self.assertRaises(AllocationError):
            allocate(payment, [(good.pk, Decimal("100.00")), (bad.pk, Decimal("500.00"))])
        good.refresh_from_db()
        self.assertEqual(good.paid_amount, Decimal("0"))
        self.assertEqual(PaymentAllocation.objects.count(), 0)

    def test_another_customers_invoice_is_refused(self):
        other = Customer.objects.create(full_name="Someone Else", email="e@x.com")
        theirs = Invoice.objects.create(customer=other, status=Invoice.Status.UNPAID, date_due=self.today)
        payment = self._payment("100.00")
        with self.assertRaises(AllocationError):
            allocate(payment, [(theirs.pk, Decimal("100.00"))])

    def test_a_proforma_is_not_a_debt(self):
        proforma = self._invoice("300.00", status=Invoice.Status.PROFORMA)
        payment = self._payment("300.00")
        with self.assertRaises(AllocationError):
            allocate(payment, [(proforma.pk, Decimal("300.00"))])
        self.assertNotIn(proforma.pk, [i.pk for i in open_invoices(self.customer)])

    def test_a_negative_adjustment_cannot_be_allocated(self):
        invoice = self._invoice("300.00")
        payment = self._payment("-300.00")
        with self.assertRaises(AllocationError):
            allocate(payment, [(invoice.pk, Decimal("300.00"))])

    def test_auto_allocate_fills_oldest_due_first(self):
        old = self._invoice("400.00", due_offset=-30)
        new = self._invoice("400.00", due_offset=-1)
        payment = self._payment("500.00")

        auto_allocate(payment, user=self.staff)

        old.refresh_from_db()
        new.refresh_from_db()
        self.assertEqual(old.paid_amount, Decimal("400.00"))
        self.assertEqual(old.status, Invoice.Status.PAID)
        self.assertEqual(new.paid_amount, Decimal("100.00"))
        self.assertEqual(new.status, Invoice.Status.UNPAID)
        self.assertEqual(unallocated_amount(payment), Decimal("0"))

    def test_undoing_one_allocation_returns_the_money(self):
        invoice = self._invoice("400.00")
        payment = self._payment("400.00")
        allocation = allocate(payment, [(invoice.pk, Decimal("400.00"))])[0]

        unallocate(allocation)

        invoice.refresh_from_db()
        self.assertEqual(invoice.paid_amount, Decimal("0"))
        self.assertEqual(invoice.status, Invoice.Status.UNPAID)
        self.assertEqual(unallocated_amount(payment), Decimal("400.00"))

    def test_allocation_never_moves_the_balance(self):
        invoice = self._invoice("400.00")
        payment = self._payment("400.00")
        self.customer.refresh_from_db()
        before = self.customer.balance

        allocate(payment, [(invoice.pk, Decimal("400.00"))])

        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, before)

    def test_deleting_an_allocated_payment_puts_every_invoice_back(self):
        first = self._invoice("400.00")
        second = self._invoice("600.00")
        payment = self._payment("1000.00")
        allocate(payment, [(first.pk, Decimal("400.00")), (second.pk, Decimal("600.00"))])

        payment.reverse_ledger_effect()
        payment.delete()

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.paid_amount, Decimal("0"))
        self.assertEqual(second.paid_amount, Decimal("0"))
        self.assertEqual(first.status, Invoice.Status.UNPAID)
        self.assertEqual(second.status, Invoice.Status.UNPAID)
        self.assertEqual(PaymentAllocation.objects.count(), 0)


class AllocationApiTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        User = get_user_model()
        self.staff = User.objects.create_user(
            username="mapper", password="x", role="admin", email="mapper@example.com"
        )
        self.client = APIClient()
        self.client.force_authenticate(self.staff)
        self.customer = Customer.objects.create(
            full_name="Hennenman Car Wash", customer_id="CUS-000003", email="wash@example.com"
        )
        config = CustomerBillingConfig.objects.get_or_create(customer=self.customer)[0]
        config.send_billing_notifications = False
        config.save()
        self.invoice = Invoice.objects.create(
            customer=self.customer, status=Invoice.Status.UNPAID, date_due=self.today
        )
        InvoiceItem.objects.create(
            invoice=self.invoice, item_type=InvoiceItem.ItemType.CUSTOM, description="Service",
            quantity=1, unit_price=Decimal("500.00"), tax_rate_pct=Decimal("0"),
        )
        self.invoice.recalc_totals()
        self.payment = Payment.objects.create(
            customer=self.customer, amount=Decimal("500.00"), method=Payment.Method.BANK_TRANSFER
        )

    def test_open_invoices_lists_what_is_owed(self):
        response = self.client.get("/api/payments/open-invoices/?customer=%s" % self.customer.pk)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["invoices"]), 1)
        self.assertEqual(response.data["invoices"][0]["balance_due"], "500.00")

    def test_open_invoices_needs_a_customer(self):
        self.assertEqual(self.client.get("/api/payments/open-invoices/").status_code, 400)

    def test_posting_a_mapping_settles_the_invoice(self):
        response = self.client.post(
            "/api/payments/%s/allocations/" % self.payment.pk,
            {"allocations": [{"invoice": self.invoice.pk, "amount": "500.00"}]},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["unallocated"], "0.00")
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, Invoice.Status.PAID)

    def test_over_allocation_is_a_400_with_a_readable_reason(self):
        response = self.client.post(
            "/api/payments/%s/allocations/" % self.payment.pk,
            {"allocations": [{"invoice": self.invoice.pk, "amount": "900.00"}]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("unallocated", response.data["detail"])
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.paid_amount, Decimal("0"))

    def test_auto_flag_fills_it(self):
        response = self.client.post(
            "/api/payments/%s/allocations/" % self.payment.pk, {"auto": True}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["allocated"], "500.00")

    def test_delete_clears_the_mapping(self):
        self.client.post(
            "/api/payments/%s/allocations/" % self.payment.pk, {"auto": True}, format="json"
        )
        response = self.client.delete("/api/payments/%s/allocations/" % self.payment.pk)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["allocations"], [])
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.paid_amount, Decimal("0"))

    def test_replace_re_maps_without_double_counting(self):
        self.client.post(
            "/api/payments/%s/allocations/" % self.payment.pk,
            {"allocations": [{"invoice": self.invoice.pk, "amount": "200.00"}]}, format="json",
        )
        response = self.client.post(
            "/api/payments/%s/allocations/" % self.payment.pk,
            {"replace": True, "allocations": [{"invoice": self.invoice.pk, "amount": "500.00"}]},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.paid_amount, Decimal("500.00"))

    def test_a_customer_cannot_map_their_own_payments(self):
        User = get_user_model()
        portal_user = User.objects.create_user(
            username="cust", password="x", role="customer", email="c@x.com"
        )
        client = APIClient()
        client.force_authenticate(portal_user)
        response = client.post(
            "/api/payments/%s/allocations/" % self.payment.pk, {"auto": True}, format="json"
        )
        self.assertIn(response.status_code, (403, 404))
