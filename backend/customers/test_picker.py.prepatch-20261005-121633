"""The picker endpoint, and the truncation it exists to end.

Four pickers -- Finance's invoice and credit dialogs, Services, Tickets --
asked for `?page_size=1000`, and config.pagination caps page_size at 500.
DRF clamps silently, so they were handed the first 500 customers by name
with nothing saying so. Searching a picker for a customer whose surname
sorted past roughly the 500th returned "Nothing matches", and the invoice,
credit, service or ticket simply could not be raised for them. On 1,592
customers that is about 1,092 people who could not be picked.

The bulk-email page had the same bug with a worse outcome: it asked for
`page_size=<count>`, got 500 back, and the button that said "Select all
1592 matching" sent to 500 -- so a payment-reminder or suspension-notice
run reached the first 500 alphabetically while the operator believed
everyone had been told.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from customers.models import Customer, Partner

User = get_user_model()


class PickerEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.staff = User.objects.create_user(
            username="picker-staff", password="pw-for-tests", role=User.Role.SUPPORT
        )
        self.client.force_authenticate(self.staff)

    def _bulk(self, n, prefix="Cust"):
        # bulk_create bypasses save(), which is what generates
        # customer_id -- so they all come out "" and collide on the unique
        # index. Given explicitly here.
        Customer.objects.bulk_create([
            Customer(
                full_name=f"{prefix} {i:04d}",
                email=f"{prefix.lower()}{i}@example.com",
                customer_id=f"{prefix.upper()[:3]}-{i:06d}",
            )
            for i in range(n)
        ])

    def test_it_returns_more_than_the_pagination_cap(self):
        """The whole point. 600 > max_page_size of 500."""
        self._bulk(600)
        res = self.client.get("/api/customers/picker/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data["results"]), 600)
        self.assertEqual(res.data["count"], 600)

    def test_the_paginated_list_really_does_clamp(self):
        """The bug being fixed, asserted directly -- so nobody 'simplifies'
        the picker back to ?page_size=1000."""
        self._bulk(600)
        res = self.client.get("/api/customers/?page_size=1000")
        self.assertEqual(len(res.data["results"]), 500)
        self.assertEqual(res.data["count"], 600)

    def test_it_carries_only_the_fields_a_picker_needs(self):
        Customer.objects.create(full_name="Solo Sam", email="sam@example.com")
        row = self.client.get("/api/customers/picker/").data["results"][0]
        self.assertEqual(set(row), {"id", "full_name", "customer_id", "email"})

    def test_it_is_ordered_by_name(self):
        for name in ("Zulu Zanele", "Adams Ayanda", "Mokoena Musa"):
            Customer.objects.create(full_name=name, email=f"{name.split()[0]}@example.com")
        names = [r["full_name"] for r in self.client.get("/api/customers/picker/").data["results"]]
        self.assertEqual(names, sorted(names))

    def test_a_customer_sorting_last_is_reachable(self):
        """The concrete symptom: searching a picker for 'Zulu' used to
        return nothing because they sorted past the clamp."""
        self._bulk(600, prefix="Aaa")
        Customer.objects.create(full_name="Zulu Zanele", email="zulu@example.com")
        names = [r["full_name"] for r in self.client.get("/api/customers/picker/").data["results"]]
        self.assertIn("Zulu Zanele", names)

    def test_it_honours_search_and_filters(self):
        Customer.objects.create(full_name="Findable Fiona", email="f@example.com")
        Customer.objects.create(full_name="Hidden Hannah", email="h@example.com")
        res = self.client.get("/api/customers/picker/?search=Findable")
        names = [r["full_name"] for r in res.data["results"]]
        self.assertEqual(names, ["Findable Fiona"])


class PickerScopingTests(TestCase):
    """A convenience endpoint must not become a way around partner
    visibility -- the exact shape of the bulk-delete bug."""

    def setUp(self):
        self.client = APIClient()
        self.acme = Partner.objects.create(name="Acme Fibre")
        self.rival = Partner.objects.create(name="Rival Networks")
        self.mine = Customer.objects.create(
            full_name="Acme Customer", email="ac@example.com", partner=self.acme
        )
        self.theirs = Customer.objects.create(
            full_name="Rival Customer", email="rc@example.com", partner=self.rival
        )
        self.direct = Customer.objects.create(
            full_name="Direct Customer", email="dc@example.com", partner=None
        )

    def _names_for(self, user):
        self.client.force_authenticate(user)
        res = self.client.get("/api/customers/picker/")
        self.assertEqual(res.status_code, 200, res.data)
        return {r["full_name"] for r in res.data["results"]}

    def test_a_restricted_staff_member_sees_only_their_partner(self):
        restricted = User.objects.create_user(
            username="acme-only", password="pw-for-tests",
            role=User.Role.SUPPORT, allowed_partners=[self.acme.id],
        )
        names = self._names_for(restricted)
        self.assertIn("Acme Customer", names)
        self.assertIn("Direct Customer", names)   # no partner, belongs to nobody
        self.assertNotIn("Rival Customer", names)

    def test_unrestricted_staff_see_everyone(self):
        hq = User.objects.create_user(
            username="hq-picker", password="pw-for-tests", role=User.Role.SUPPORT
        )
        self.assertIn("Rival Customer", self._names_for(hq))

    def test_a_customer_role_user_is_refused_outright(self):
        """The picker is a staff tool -- it is gated on IsStaffMember, the
        same tier as the customer list itself, so a portal user never
        reaches the queryset at all."""
        login = User.objects.create_user(
            username="portal-picker", password="pw-for-tests", role=User.Role.CUSTOMER
        )
        self.client.force_authenticate(login)
        self.assertEqual(self.client.get("/api/customers/picker/").status_code, 403)

    def test_staff_without_the_customers_section_can_still_use_it(self):
        """A Finance-only staff member has to be able to name a customer on
        a new invoice. Gating this on the Customers section would have
        been worse than the truncation it replaced."""
        finance_only = User.objects.create_user(
            username="finance-only", password="pw-for-tests",
            role=User.Role.ACCOUNTS, allowed_sections=["finance"],
        )
        self.assertIn("Acme Customer", self._names_for(finance_only))

    def test_it_requires_authentication(self):
        self.client.force_authenticate(None)
        res = self.client.get("/api/customers/picker/")
        self.assertIn(res.status_code, (401, 403))
