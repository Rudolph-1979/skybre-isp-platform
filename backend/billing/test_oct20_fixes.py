"""Fixes found while writing Conecto's answers (20 Oct 2026, Rudolph: "Yes
please"): editing a discounted service as ordinary staff, cancelling an
invoice, and the public holidays list."""
import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from billing.models import Invoice, InvoiceItem, PublicHoliday, Service, Tariff
from customers.models import Customer

User = get_user_model()


def client_for(role, sections=("customers", "services", "finance")):
    c = APIClient()
    c.force_authenticate(User.objects.create_user(username=f"u-{role}", password="x", role=role,
                                                  allowed_sections=list(sections)))
    return c


class Base(TestCase):
    def setUp(self):
        self.tariff = Tariff.objects.create(name="Wireless 8", price=Decimal("400.00"), tax_rate_pct=Decimal("15.00"))
        self.c = Customer.objects.create(full_name="Ann Able", status=Customer.Status.ACTIVE)
        self.svc = Service.objects.create(
            customer=self.c, tariff=self.tariff, status=Service.Status.ACTIVE, start_date=datetime.date(2026, 1, 1),
            discount_type=Service.DiscountType.AMOUNT, discount_value=Decimal("50.00"), discount_reason="Loyal",
        )


class DiscountEditTests(Base):
    def payload(self, **over):
        body = {"discount_type": "amount", "discount_value": "50.00", "discount_starts_on": None,
                "discount_ends_on": None, "discount_reason": "Loyal", "access_detail": "Sector B"}
        body.update(over)
        return body

    def test_support_can_save_other_fields_when_discount_unchanged(self):
        res = client_for("support").patch(f"/api/services/{self.svc.pk}/", self.payload(), format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.svc.refresh_from_db()
        self.assertEqual(self.svc.access_detail, "Sector B")

    def test_support_still_cannot_change_the_discount(self):
        res = client_for("support").patch(f"/api/services/{self.svc.pk}/", self.payload(discount_value="80"), format="json")
        self.assertEqual(res.status_code, 400)
        self.assertIn("Only Management", str(res.data))

    def test_management_can_change_it(self):
        res = client_for("management").patch(f"/api/services/{self.svc.pk}/", self.payload(discount_value="80"), format="json")
        self.assertEqual(res.status_code, 200, res.data)


class CancelInvoiceTests(Base):
    def doc(self, status, paid=Decimal("0")):
        inv = Invoice.objects.create(customer=self.c, status=status, date_due=datetime.date(2026, 10, 30))
        InvoiceItem.objects.create(invoice=inv, description="Router", quantity=1, unit_price=Decimal("100.00"),
                                   tax_rate_pct=Decimal("15.00"))
        inv.recalc_totals()
        inv.apply_balance_debit()
        if paid:
            Invoice.objects.filter(pk=inv.pk).update(paid_amount=paid)
        inv.refresh_from_db()
        return inv

    def test_quote_by_finance_staff(self):
        q = self.doc(Invoice.Status.QUOTE)
        res = client_for("accounts").post(f"/api/invoices/{q.pk}/cancel/", {"reason": "Declined"}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        q.refresh_from_db()
        self.assertEqual(q.status, Invoice.Status.CANCELLED)
        self.assertIn("Declined", q.note)

    def test_real_invoice_needs_management_and_takes_balance_back(self):
        inv = self.doc(Invoice.Status.UNPAID)
        self.c.refresh_from_db()
        self.assertEqual(self.c.balance, Decimal("115.00"))
        res = client_for("accounts").post(f"/api/invoices/{inv.pk}/cancel/", {"reason": "Wrong client"}, format="json")
        self.assertEqual(res.status_code, 403)
        res = client_for("management").post(f"/api/invoices/{inv.pk}/cancel/", {"reason": "Wrong client"}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.c.refresh_from_db()
        self.assertEqual(self.c.balance, Decimal("0.00"))

    def test_refused_with_payment_or_without_reason(self):
        inv = self.doc(Invoice.Status.UNPAID, paid=Decimal("50.00"))
        mgmt = client_for("management")
        self.assertEqual(mgmt.post(f"/api/invoices/{inv.pk}/cancel/", {"reason": "x"}, format="json").status_code, 400)
        inv2 = self.doc(Invoice.Status.UNPAID)
        self.assertEqual(mgmt.post(f"/api/invoices/{inv2.pk}/cancel/", {"reason": " "}, format="json").status_code, 400)


class PublicHolidayTests(TestCase):
    def test_admin_edits_staff_reads(self):
        admin = client_for("admin", sections=())
        res = admin.post("/api/public-holidays/", {"date": "2026-12-16", "name": "Day of Reconciliation"}, format="json")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertFalse(PublicHoliday.objects.get().seeded)
        support = client_for("support", sections=())
        self.assertEqual(support.get("/api/public-holidays/?year=2026").status_code, 200)
        self.assertEqual(len(support.get("/api/public-holidays/?year=2026").data), 1)
        self.assertEqual(support.post("/api/public-holidays/", {"date": "2026-12-25", "name": "x"}, format="json").status_code, 403)
        self.assertEqual(admin.post("/api/public-holidays/", {"date": "2026-12-16", "name": "Dup"}, format="json").status_code, 400)

    def test_editing_a_seeded_day_marks_it_by_hand(self):
        h = PublicHoliday.objects.create(date=datetime.date(2026, 9, 24), name="Heritage Day", seeded=True)
        res = client_for("admin", sections=()).patch(f"/api/public-holidays/{h.pk}/", {"name": "Heritage Day (moved)"}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        h.refresh_from_db()
        self.assertFalse(h.seeded)
