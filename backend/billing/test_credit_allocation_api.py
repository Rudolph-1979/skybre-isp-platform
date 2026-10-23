"""Re-allocating an approved credit by hand (5 Oct 2026) -- the credit
counterpart of the payment Allocate screen."""
import datetime
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.testing import ALL_SECTIONS
from customers.models import Customer

from .models import CreditAllocation, CreditRequest, Invoice

User = get_user_model()


class CreditAllocationApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_user(
            username="boss-ca", password="x", role=User.Role.MANAGEMENT, allowed_sections=ALL_SECTIONS,
        ))
        self.customer = Customer.objects.create(full_name="Cy", email="c@x.com", customer_id="CUS-CA1")
        today = datetime.date.today()
        self.old = self._inv("100", today - datetime.timedelta(days=30))
        self.new = self._inv("100", today)
        self.credit = CreditRequest.objects.create(customer=self.customer, amount=D("100"), reason="x")
        res = self.client.post(f"/api/credit-requests/{self.credit.pk}/approve/")
        self.assertEqual(res.status_code, 200, res.data)
        self.url = f"/api/credit-requests/{self.credit.pk}/allocations/"

    def _inv(self, total, due):
        inv = Invoice.objects.create(customer=self.customer, status="unpaid", date_due=due)
        Invoice.objects.filter(pk=inv.pk).update(total=D(total))
        return inv

    def _paid(self, inv):
        inv.refresh_from_db()
        return inv.paid_amount

    def test_approval_settled_the_oldest_and_get_shows_it(self):
        body = self.client.get(self.url).json()
        self.assertEqual([a["invoice"] for a in body["allocations"]], [self.old.pk])
        self.assertEqual(body["unallocated"], "0.00")

    def test_moving_the_credit_to_another_invoice(self):
        res = self.client.post(self.url, {"replace": True, "allocations": [{"invoice": self.new.pk, "amount": "100.00"}]}, format="json")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual((self._paid(self.old), self._paid(self.new)), (D("0"), D("100")))
        self.new.refresh_from_db()
        self.assertEqual(self.new.status, "paid")

    def test_auto_puts_it_back_oldest_first(self):
        self.client.post(self.url, {"replace": True, "allocations": [{"invoice": self.new.pk, "amount": "100"}]}, format="json")
        self.client.post(self.url, {"replace": True, "auto": True}, format="json")
        self.assertEqual((self._paid(self.old), self._paid(self.new)), (D("100"), D("0")))

    def test_clearing_leaves_it_as_account_credit(self):
        res = self.client.delete(self.url)
        self.assertEqual(res.json()["unallocated"], "100.00")
        self.assertEqual(self._paid(self.old), D("0"))

    def test_over_allocating_is_refused_and_changes_nothing(self):
        res = self.client.post(self.url, {"replace": True, "allocations": [
            {"invoice": self.old.pk, "amount": "60"}, {"invoice": self.new.pk, "amount": "60"}]}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self._paid(self.old), D("100"))  # rolled back
        self.assertEqual(CreditAllocation.objects.filter(credit=self.credit).count(), 1)

    def test_a_bad_body_changes_nothing(self):
        res = self.client.post(self.url, {"replace": True, "allocations": "nope"}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self._paid(self.old), D("100"))

    def test_legacy_and_pending_credits_are_refused(self):
        legacy = CreditRequest.objects.create(customer=self.customer, amount=D("10"), reason="old",
                                              status="approved", allocation_tracked=False)
        res = self.client.post(f"/api/credit-requests/{legacy.pk}/allocations/", {"auto": True}, format="json")
        self.assertEqual(res.status_code, 400)
        pending = CreditRequest.objects.create(customer=self.customer, amount=D("10"), reason="p")
        res = self.client.post(f"/api/credit-requests/{pending.pk}/allocations/", {"auto": True}, format="json")
        self.assertEqual(res.status_code, 400)

    def test_the_credits_list_shows_what_it_settles(self):
        row = self.client.get(f"/api/credit-requests/{self.credit.pk}/").json()
        self.assertTrue(row["allocation_tracked"])
        self.assertEqual(row["allocations_detail"][0]["invoice"], self.old.pk)
