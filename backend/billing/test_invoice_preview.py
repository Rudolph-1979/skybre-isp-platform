"""Customer -> Services -> Preview invoice (George, 13 Oct 2026)."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from billing.models import CustomerBillingConfig, Invoice, Service, Tariff
from customers.models import Customer

User = get_user_model()


class InvoicePreviewTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.tariff = Tariff.objects.create(name="SCF 20 Mbps", price=Decimal("399.00"), tax_rate_pct=Decimal("15"))
        self.customer = Customer.objects.create(full_name="Preview Client", customer_id="CUS-009002",
                                                status=Customer.Status.ACTIVE)
        config = CustomerBillingConfig.for_customer(self.customer)
        config.next_billing_date = self.today
        config.save()
        Service.objects.create(customer=self.customer, tariff=self.tariff, status=Service.Status.ACTIVE,
                               start_date="2026-01-01", discount_type="amount", discount_value=Decimal("100.00"))
        self.api = APIClient()
        self.api.force_authenticate(User.objects.create_user(username="adm", password="pw-for-tests-123", role="admin"))

    def test_preview_lines_and_total_and_nothing_saved(self):
        res = self.api.get(f"/api/customer-billing-config/{self.customer.pk}/invoice-preview/")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["date"], self.today.isoformat())
        self.assertEqual([l["unit_price"] for l in res.data["lines"]], ["399.00", "-100.00"])
        self.assertEqual(res.data["total"], "343.85")   # (399 - 100) * 1.15
        self.assertFalse(Invoice.objects.filter(customer=self.customer).exists())

    def test_date_before_start_bills_nothing_and_bad_date_refused(self):
        res = self.api.get(f"/api/customer-billing-config/{self.customer.pk}/invoice-preview/?date=2025-01-01")
        self.assertEqual(res.data["lines"], [])
        res = self.api.get(f"/api/customer-billing-config/{self.customer.pk}/invoice-preview/?date=nope")
        self.assertEqual(res.status_code, 400)

    def test_rand_up_is_one_line_with_an_office_note(self):
        svc = Service.objects.get(customer=self.customer)
        svc.discount_type = "surcharge"
        svc.discount_value = Decimal("121.87")
        svc.save()
        res = self.api.get(f"/api/customer-billing-config/{self.customer.pk}/invoice-preview/")
        self.assertEqual([l["unit_price"] for l in res.data["lines"]], ["520.87"])
        self.assertIn("R121.87", res.data["lines"][0]["office_note"])
        self.assertNotIn("Special pricing", res.data["lines"][0]["description"])


class InvoicePreviewPdfTests(InvoicePreviewTests):
    """The PDF is the real invoice document, built and rolled back."""

    def test_pdf_is_returned_and_nothing_is_kept(self):
        from billing.models import IssuedNumberHighWater

        marks = list(IssuedNumberHighWater.objects.values_list("prefix", "last_seq"))
        cfg_before = CustomerBillingConfig.for_customer(self.customer).next_billing_date
        res = self.api.get(f"/api/customer-billing-config/{self.customer.pk}/invoice-preview/pdf/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Type"], "application/pdf")
        self.assertTrue(res.content.startswith(b"%PDF"))
        self.assertFalse(Invoice.objects.filter(customer=self.customer).exists())
        self.assertEqual(list(IssuedNumberHighWater.objects.values_list("prefix", "last_seq")), marks)
        self.assertEqual(CustomerBillingConfig.for_customer(self.customer).next_billing_date, cfg_before)

    def test_nothing_to_bill_is_404(self):
        res = self.api.get(f"/api/customer-billing-config/{self.customer.pk}/invoice-preview/pdf/?date=2025-01-01")
        self.assertEqual(res.status_code, 404)


class RecurringChargePricingTests(TestCase):
    """Custom pricing on a client's recurring services (13 Oct 2026)."""

    def setUp(self):
        from billing.models import CustomerRecurringCharge, RecurringCharge

        self.today = timezone.localdate()
        self.customer = Customer.objects.create(full_name="Charge Client", customer_id="CUS-009003",
                                                status=Customer.Status.ACTIVE)
        self.charge = RecurringCharge.objects.create(name="Email Hosting", price=Decimal("60.00"),
                                                     tax_rate_pct=Decimal("15"))
        self.link = CustomerRecurringCharge.objects.create(customer=self.customer, charge=self.charge,
                                                           start_date="2026-01-01")
        self.api = APIClient()
        self.api.force_authenticate(User.objects.create_user(username="adm2", password="pw-for-tests-123", role="admin"))

    def _lines(self):
        from billing.invoice_lines import customer_invoice_lines

        return [(l["description"], l["unit_price"]) for l in customer_invoice_lines(self.customer, self.today)]

    def test_rand_up_is_folded_into_the_line(self):
        res = self.api.patch(f"/api/customer-recurring-charges/{self.link.pk}/",
                             {"discount_type": "surcharge", "discount_value": "20.00"}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(self._lines(), [("Email Hosting", Decimal("80.0000"))])
        self.assertEqual(res.data["net_price_incl_vat"], "92.00")

    def test_discount_is_its_own_line_and_capped(self):
        res = self.api.patch(f"/api/customer-recurring-charges/{self.link.pk}/",
                             {"discount_type": "percentage", "discount_value": "50"}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(self._lines(), [("Email Hosting", Decimal("60.00")), ("Discount (50%)", Decimal("-30.00"))])
        res = self.api.patch(f"/api/customer-recurring-charges/{self.link.pk}/",
                             {"discount_type": "amount", "discount_value": "61.00"}, format="json")
        self.assertEqual(res.status_code, 400)

    def test_client_never_sees_the_uplift(self):
        from billing.serializers import CustomerRecurringChargeSerializer

        self.link.discount_type = "surcharge"
        self.link.discount_value = Decimal("20.00")
        self.link.save()
        client_user = User.objects.create_user(username="cl2", password="pw-for-tests-123", role="customer")

        class Req:
            user = client_user

        data = CustomerRecurringChargeSerializer(self.link, context={"request": Req()}).data
        self.assertEqual((data["price"], data["price_incl_vat"], data["discount_type"]), ("80.00", "92.00", "none"))
        self.assertNotIn("surcharge_now_incl_vat", data)
