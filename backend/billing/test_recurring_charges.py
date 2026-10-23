"""Standard recurring charges -- IP Rental, domain, email, web hosting.

A reusable catalog (RecurringCharge) given to a CLIENT via
CustomerRecurringCharge (Rudolph, 1 Oct: "Recurring billing adding should
not be dependable on a service, some clients only take a domain, email or
web hosting"). Billed every cycle as its own invoice line at the catalog's
live price and tax rate x quantity, from start_date until end_date, with
or without any service -- built through the same billing/invoice_lines.py
module the engine and the finance-approval preview both read, so the two
cannot disagree.
"""
import datetime
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate

from accounts.models import User
from billing.gate_testing import ApprovalGateOpenMixin
from billing.invoice_lines import customer_invoice_lines
from billing.models import (
    CustomerBillingConfig, CustomerRecurringCharge, Invoice, InvoiceItem, RecurringCharge, Service, Tariff,
)
from billing.preview_export import project_run
from billing.recurring import run_recurring_billing
from billing.views import RecurringChargeViewSet
from customers.models import Customer, Partner
from notifications.models import EmailTemplate


class RecurringChargePartnerRuleTests(TestCase):
    def setUp(self):
        self.mtd = Partner.objects.create(name="MTD")
        self.vuma = Partner.objects.create(name="Vumatel")

    def test_a_shared_charge_is_available_to_everyone(self):
        charge = RecurringCharge.objects.create(name="IP Rental", price=Decimal("50"), partner=None)
        self.assertTrue(charge.available_to(self.mtd.pk))
        self.assertTrue(charge.available_to(None))

    def test_a_partner_charge_is_only_available_to_that_partner(self):
        charge = RecurringCharge.objects.create(name="IP Rental", price=Decimal("50"), partner=self.mtd)
        self.assertTrue(charge.available_to(self.mtd.pk))
        self.assertFalse(charge.available_to(self.vuma.pk))

    def test_a_direct_customer_may_take_any_charge(self):
        charge = RecurringCharge.objects.create(name="IP Rental", price=Decimal("50"), partner=self.mtd)
        self.assertTrue(charge.available_to(None))


class CustomerRecurringChargeApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(username="boss", password="x", role=User.Role.ADMIN)
        self.client.force_authenticate(self.admin)
        self.mtd = Partner.objects.create(name="MTD")
        self.vuma = Partner.objects.create(name="Vumatel")
        self.customer = Customer.objects.create(full_name="Lani Grunder", email="lani@x.com", partner=self.mtd)
        self.mtd_charge = RecurringCharge.objects.create(name="Web Hosting", price=Decimal("99"), partner=self.mtd)
        self.vuma_charge = RecurringCharge.objects.create(name="Domain", price=Decimal("30"), partner=self.vuma)

    def _add(self, **extra):
        payload = {"customer": self.customer.pk, "charge": self.mtd_charge.pk,
                   "start_date": "2026-10-01", **extra}
        return self.client.post("/api/customer-recurring-charges/", payload, format="json")

    def test_a_client_with_no_service_can_be_given_a_charge(self):
        res = self._add(quantity=2, note="grunder.co.za")
        self.assertEqual(res.status_code, 201, res.content)
        body = res.json()
        self.assertEqual(body["charge_name"], "Web Hosting")
        self.assertEqual(body["price"], "99.00")
        self.assertEqual(body["quantity"], 2)
        self.assertFalse(Service.objects.filter(customer=self.customer).exists())

    def test_another_partners_charge_is_refused(self):
        res = self._add(charge=self.vuma_charge.pk)
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn("Vumatel", str(res.json()["charge"]))

    def test_end_before_start_is_refused(self):
        res = self._add(end_date="2026-09-30")
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn("end_date", res.json())

    def test_listed_per_client(self):
        self._add()
        other = Customer.objects.create(full_name="Other", email="o@x.com", partner=self.mtd)
        CustomerRecurringCharge.objects.create(customer=other, charge=self.mtd_charge, start_date="2026-10-01")
        rows = self.client.get(f"/api/customer-recurring-charges/?customer={self.customer.pk}").json()["results"]
        self.assertEqual(len(rows), 1)

    def test_a_charge_can_be_ended_and_deleted(self):
        link_id = self._add().json()["id"]
        res = self.client.patch(f"/api/customer-recurring-charges/{link_id}/", {"end_date": "2026-11-01"}, format="json")
        self.assertEqual(res.status_code, 200, res.content)
        res = self.client.delete(f"/api/customer-recurring-charges/{link_id}/")
        self.assertEqual(res.status_code, 204)


class RecurringChargeViewSetTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(username="boss", password="x", role=User.Role.ADMIN)
        self.client.force_authenticate(self.admin)
        self.mtd = Partner.objects.create(name="MTD")
        self.vuma = Partner.objects.create(name="Vumatel")
        self.customer = Customer.objects.create(full_name="Del Dave", email="d@x.com", partner=self.mtd)

    def test_the_catalog_can_be_listed_and_created_through_the_api(self):
        res = self.client.post(
            "/api/recurring-charges/", {"name": "IP Rental", "price": "50.00", "tax_rate_pct": "15"}, format="json",
        )
        self.assertEqual(res.status_code, 201, res.content)
        listed = self.client.get("/api/recurring-charges/").json()
        self.assertEqual(len(listed["results"]), 1)
        self.assertEqual(listed["results"][0]["client_count"], 0)

    def test_the_list_narrows_to_a_customers_partner(self):
        mtd_charge = RecurringCharge.objects.create(name="IP Rental", price=Decimal("50"), partner=self.mtd)
        RecurringCharge.objects.create(name="Domain Rental", price=Decimal("30"), partner=self.vuma)
        shared = RecurringCharge.objects.create(name="Static IP", price=Decimal("75"), partner=None)
        res = self.client.get(f"/api/recurring-charges/?for_customer={self.customer.pk}")
        self.assertEqual({c["id"] for c in res.json()["results"]}, {mtd_charge.pk, shared.pk})

    def _delete(self, charge):
        request = APIRequestFactory().delete(f"/recurring-charges/{charge.pk}/")
        force_authenticate(request, user=self.admin)
        return RecurringChargeViewSet.as_view({"delete": "destroy"})(request, pk=charge.pk)

    def test_an_unused_charge_deletes(self):
        charge = RecurringCharge.objects.create(name="Spare", price=Decimal("1"))
        self.assertEqual(self._delete(charge).status_code, 204)

    def test_a_charge_a_client_has_is_refused_with_a_useful_message(self):
        charge = RecurringCharge.objects.create(name="IP Rental", price=Decimal("50"))
        CustomerRecurringCharge.objects.create(customer=self.customer, charge=charge, start_date="2026-10-01")
        response = self._delete(charge)
        self.assertEqual(response.status_code, 400)
        self.assertIn("1 client has it", response.data["detail"])
        self.assertTrue(RecurringCharge.objects.filter(pk=charge.pk).exists())

    def test_client_count_counts_clients(self):
        charge = RecurringCharge.objects.create(name="IP Rental", price=Decimal("50"))
        CustomerRecurringCharge.objects.create(customer=self.customer, charge=charge, start_date="2026-10-01")
        listed = self.client.get("/api/recurring-charges/").json()
        self.assertEqual(listed["results"][0]["client_count"], 1)


class RecurringChargeBillingTests(ApprovalGateOpenMixin, TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        EmailTemplate.objects.get_or_create(
            key=EmailTemplate.Key.INVOICE, defaults={"subject": "Invoice", "body_html": "<p>x</p>"},
        )
        self.tariff = Tariff.objects.create(
            name="MTD 250 Mbps", price=Decimal("999.00"), tax_rate_pct=Decimal("15"),
            speed_download_kbps=256000, speed_upload_kbps=256000,
        )
        self.customer = self._billable_customer("Rudolph Grunder", "CUS-001598")
        self.service = Service.objects.create(
            customer=self.customer, tariff=self.tariff, status=Service.Status.ACTIVE, start_date="2026-01-01",
        )
        self.ip_rental = RecurringCharge.objects.create(name="IP Rental", price=Decimal("50.00"), tax_rate_pct=Decimal("15"))
        self.hosting = RecurringCharge.objects.create(name="Web Hosting", price=Decimal("30.00"), tax_rate_pct=Decimal("15"))

    def _billable_customer(self, name, code):
        customer = Customer.objects.create(
            full_name=name, customer_id=code, email=f"{code}@example.com", status=Customer.Status.ACTIVE,
        )
        config = CustomerBillingConfig.objects.get_or_create(customer=customer)[0]
        config.billing_enabled = True
        config.auto_create_invoices = True
        config.send_billing_notifications = False
        config.billing_day = self.today.day
        config.next_billing_date = self.today
        config.save()
        return customer

    def _give(self, customer, charge, **extra):
        return CustomerRecurringCharge.objects.create(
            customer=customer, charge=charge, start_date=extra.pop("start_date", self.today - datetime.timedelta(days=30)),
            **extra,
        )

    def _billed_invoice(self, customer=None):
        run_recurring_billing(self.today, commit=True)
        return Invoice.objects.get(customer=customer or self.customer)

    def test_a_charge_is_its_own_line_after_the_plan(self):
        self._give(self.customer, self.ip_rental)
        lines = customer_invoice_lines(self.customer, self.today)
        self.assertEqual([l["kind"] for l in lines], ["tariff", "recurring_charge"])
        self.assertEqual(lines[1]["description"], "IP Rental")
        self.assertIsNone(lines[1]["service"])

    def test_several_charges_each_get_their_own_line_by_name(self):
        self._give(self.customer, self.ip_rental)
        self._give(self.customer, self.hosting)
        descriptions = [l["description"] for l in customer_invoice_lines(self.customer, self.today)]
        self.assertEqual(descriptions, ["MTD 250 Mbps (Monthly)", "IP Rental", "Web Hosting"])

    def test_a_client_with_only_a_charge_and_no_service_is_invoiced(self):
        hosting_only = self._billable_customer("Domain Only", "CUS-009500")
        self._give(hosting_only, self.hosting, quantity=2, note="example.co.za")
        invoice = self._billed_invoice(hosting_only)
        item = InvoiceItem.objects.get(invoice=invoice)
        self.assertEqual(item.item_type, InvoiceItem.ItemType.RECURRING_CHARGE)
        self.assertEqual(item.recurring_charge, self.hosting)
        self.assertIsNone(item.service)
        self.assertEqual(item.description, "Web Hosting — example.co.za")
        self.assertEqual(item.quantity, Decimal("2"))
        # 2 x 30 = 60, +15% = 69.00
        self.assertEqual(invoice.total, Decimal("69.00"))

    def test_a_client_with_nothing_is_still_not_invoiced(self):
        nothing = self._billable_customer("Nothing", "CUS-009501")
        run_recurring_billing(self.today, commit=True)
        self.assertFalse(Invoice.objects.filter(customer=nothing).exists())

    def test_not_billed_before_its_start_date(self):
        self._give(self.customer, self.ip_rental, start_date=self.today + datetime.timedelta(days=1))
        self.assertEqual(self._billed_invoice().subtotal, Decimal("999.00"))

    def test_not_billed_on_or_after_its_end_date(self):
        self._give(self.customer, self.ip_rental, end_date=self.today)
        self.assertEqual(self._billed_invoice().subtotal, Decimal("999.00"))

    def test_billed_the_day_before_its_end_date(self):
        self._give(self.customer, self.ip_rental, end_date=self.today + datetime.timedelta(days=1))
        self.assertEqual(self._billed_invoice().subtotal, Decimal("1049.00"))

    def test_the_invoice_total_includes_the_charge(self):
        self._give(self.customer, self.ip_rental)
        invoice = self._billed_invoice()
        self.assertEqual(invoice.subtotal, Decimal("1049.00"))
        self.assertEqual(invoice.total, Decimal("1206.35"))

    def test_the_preview_projection_agrees_with_the_invoice(self):
        self._give(self.customer, self.ip_rental)
        hosting_only = self._billable_customer("Domain Only", "CUS-009502")
        self._give(hosting_only, self.hosting, quantity=3)
        projected = project_run(self.today)["grand_total"]
        run_recurring_billing(self.today, commit=True)
        billed = sum(i.total for i in Invoice.objects.all())
        self.assertEqual(projected, billed)

    def test_deactivating_the_catalog_entry_does_not_stop_billing_it(self):
        self._give(self.customer, self.ip_rental)
        self.ip_rental.is_active = False
        self.ip_rental.save()
        self.assertEqual(self._billed_invoice().subtotal, Decimal("1049.00"))

    def test_the_charge_does_not_depend_on_the_service(self):
        """Terminating the client's only service no longer stops the charge
        -- it's the client's."""
        self._give(self.customer, self.hosting)
        Service.objects.filter(pk=self.service.pk).update(status=Service.Status.TERMINATED)
        self.assertEqual(self._billed_invoice().subtotal, Decimal("30.00"))
