"""Bulk customer deletion must respect partner visibility.

CustomerDeletionRequestViewSet.bulk_delete resolved customers straight off
the unfiltered manager -- `Customer.objects.filter(id__in=customer_ids)` --
underneath a comment that already claimed it was "scoped through the same
get_queryset a normal request would use".

Because Management is the tier that deletes immediately rather than
queueing a request, a single reseller-restricted Management account
posting a range of ids could delete the entire customer base: every other
partner's records, cascading services, RADIUS logins, invoices, payments
and tickets, for customers they cannot so much as list.

The visibility rule now lives in one place, customers.views.scope_customers_to_user,
so the list endpoint and this one cannot drift apart again.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from customers.models import Customer, CustomerDeletionRequest, Partner

User = get_user_model()


class BulkDeleteScopingTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.acme = Partner.objects.create(name="Acme Fibre")
        self.rival = Partner.objects.create(name="Rival Networks")

        self.mine = Customer.objects.create(
            full_name="Acme Customer", email="acme-c@example.com", partner=self.acme
        )
        self.theirs = Customer.objects.create(
            full_name="Rival Customer", email="rival-c@example.com", partner=self.rival
        )
        self.direct = Customer.objects.create(
            full_name="Direct Customer", email="direct-c@example.com", partner=None
        )

        # Management, so can_decide_immediately is True -- the dangerous tier.
        self.restricted = User.objects.create_user(
            username="acme-manager", password="pw-for-tests",
            role=User.Role.MANAGEMENT, allowed_partners=[self.acme.id],
        )
        self.unrestricted = User.objects.create_user(
            username="hq-manager", password="pw-for-tests", role=User.Role.MANAGEMENT
        )

    def _bulk_delete(self, user, ids):
        self.client.force_authenticate(user)
        return self.client.post(
            "/api/customer-deletion-requests/bulk-delete/",
            {"customer_ids": ids, "reason": "cleanup"},
            format="json",
        )

    def test_a_restricted_manager_cannot_delete_another_partners_customer(self):
        res = self._bulk_delete(self.restricted, [self.theirs.id])
        self.assertEqual(res.status_code, 200, res.data)
        self.assertTrue(Customer.objects.filter(pk=self.theirs.pk).exists())
        self.assertEqual(res.data["deleted"], [])
        self.assertEqual(len(res.data["skipped"]), 1)

    def test_the_whole_book_cannot_be_swept_by_posting_a_range_of_ids(self):
        """The catastrophic shape of it: every id at once."""
        every_id = list(Customer.objects.values_list("id", flat=True))
        res = self._bulk_delete(self.restricted, every_id)
        self.assertEqual(res.status_code, 200, res.data)
        self.assertTrue(Customer.objects.filter(pk=self.theirs.pk).exists())
        # Their own partner's customer and the unowned direct customer are
        # both legitimately visible to them, so those do go.
        self.assertFalse(Customer.objects.filter(pk=self.mine.pk).exists())

    def test_a_restricted_manager_can_still_delete_their_own_partners_customer(self):
        res = self._bulk_delete(self.restricted, [self.mine.id])
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["deleted"], [self.mine.id])
        self.assertFalse(Customer.objects.filter(pk=self.mine.pk).exists())

    def test_an_unrestricted_manager_still_sees_everything(self):
        res = self._bulk_delete(self.unrestricted, [self.mine.id, self.theirs.id])
        self.assertEqual(res.status_code, 200, res.data)
        self.assertCountEqual(res.data["deleted"], [self.mine.id, self.theirs.id])

    def test_a_restricted_manager_does_not_see_other_partners_deletion_requests(self):
        """A deletion request carries the customer's name, so an unscoped
        list handed a reseller the names of customers outside their
        partners."""
        CustomerDeletionRequest.objects.create(
            customer=self.theirs, reason="their business", requested_by=self.unrestricted
        )
        CustomerDeletionRequest.objects.create(
            customer=self.mine, reason="mine", requested_by=self.unrestricted
        )
        self.client.force_authenticate(self.restricted)
        res = self.client.get("/api/customer-deletion-requests/")
        self.assertEqual(res.status_code, 200)
        reasons = [r["reason"] for r in res.data["results"]]
        self.assertIn("mine", reasons)
        self.assertNotIn("their business", reasons)
