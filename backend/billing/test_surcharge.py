"""'Rand amount up' special pricing (George, 13 Oct 2026): a client charged
MORE than the plan. Folded into the plan line so the client only ever sees
the full price, and the preview projects exactly what the run bills."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from billing.gate_testing import ApprovalGateOpenMixin
from billing.models import CustomerBillingConfig, Invoice, Service, Tariff
from billing.preview_export import project_run
from billing.recurring import run_recurring_billing
from customers.models import Customer
from notifications.models import EmailTemplate


class SurchargeTests(ApprovalGateOpenMixin, TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        EmailTemplate.objects.get_or_create(
            key=EmailTemplate.Key.INVOICE, defaults={"subject": "Invoice", "body_html": "<p>x</p>"},
        )
        self.tariff = Tariff.objects.create(name="SCF 20 Mbps", price=Decimal("399.00"), tax_rate_pct=Decimal("15"))
        self.customer = Customer.objects.create(
            full_name="Up Client", customer_id="CUS-009001", email="up@example.com", status=Customer.Status.ACTIVE,
        )
        config = CustomerBillingConfig.objects.get_or_create(customer=self.customer)[0]
        config.billing_enabled = True
        config.auto_create_invoices = True
        config.send_billing_notifications = False
        config.billing_day = self.today.day
        config.next_billing_date = self.today
        config.save()
        self.service = Service.objects.create(
            customer=self.customer, tariff=self.tariff, status=Service.Status.ACTIVE, start_date="2026-01-01",
            discount_type="surcharge", discount_value=Decimal("50.00"), discount_reason="Premium install",
        )

    def test_model_figures(self):
        self.assertEqual(self.service.discount_amount_for(self.tariff.price, self.today), Decimal("0"))
        self.assertEqual(self.service.surcharge_amount_for(self.today), Decimal("50.00"))
        self.assertEqual(self.service.discount_label(), "Special pricing — Premium install (+R50.00)")

    def test_invoice_carries_a_positive_line_and_preview_matches(self):
        projected = project_run(self.today)["grand_total"]
        run_recurring_billing(self.today, commit=True)
        invoice = Invoice.objects.get(customer=self.customer)
        # (399 + 50) * 1.15
        self.assertEqual(invoice.total, Decimal("516.35"))
        self.assertEqual(Decimal(str(projected)), invoice.total)
        # ONE line at plan + uplift: the client never sees the uplift apart.
        items = list(invoice.items.all())
        self.assertEqual([i.unit_price for i in items], [Decimal("449.00")])
        self.assertNotIn("Special pricing", items[0].description)

    def test_api_figures_and_no_cap(self):
        User = get_user_model()
        admin = User.objects.create_user(username="adm", password="pw-for-tests-123", role="admin")
        api = APIClient()
        api.force_authenticate(admin)
        res = api.get(f"/api/services/{self.service.pk}/")
        self.assertEqual(res.data["surcharge_now_incl_vat"], "57.50")
        self.assertEqual(res.data["net_price_incl_vat"], "516.35")
        self.assertEqual(res.data["discount_now_incl_vat"], "0.00")
        # More than the plan price is fine for an increase.
        res = api.patch(f"/api/services/{self.service.pk}/", {"discount_type": "surcharge", "discount_value": "500.00"}, format="json")
        self.assertEqual(res.status_code, 200, res.data)

    def test_client_never_sees_the_uplift_or_our_cost(self):
        from customers.models import Customer as C  # noqa: F401

        self.tariff.cost_price = Decimal("200.00")
        self.tariff.save()
        User = get_user_model()
        client_user = User.objects.create_user(username="client1", password="pw-for-tests-123", role="customer")
        self.customer.user = client_user
        self.customer.save()
        from billing.serializers import ServiceSerializer

        class Req:
            user = client_user

        data = ServiceSerializer(self.service, context={"request": Req()}).data
        self.assertEqual(data["price"], "449.00")
        self.assertEqual(data["price_incl_vat"], "516.35")
        self.assertEqual(data["discount_type"], "none")
        self.assertEqual(data["discount_reason"], "")
        for key in ("cost", "cost_incl_vat", "surcharge_now_incl_vat"):
            self.assertNotIn(key, data)
