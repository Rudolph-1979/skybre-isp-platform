"""An imported customer has to belong to a reseller.

The customer import declared no `partner` field and nothing set one, so
every imported row landed with partner=NULL -- and
scope_customers_to_user deliberately shows partner-less customers to
EVERY staff member, because a direct customer is not owned by any
reseller.

So a reseller's whole list, imported by that reseller's own staff, became
visible to every other reseller's staff: name, address, ID number, VAT
number, phone, email, balance. That is the leak commit 0021cc6 closed on
the Finance page, walked straight back in through the importer -- and
PROJECT_STATUS records 1,592 real customers arriving this way.
"""
import io

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from customers.models import Customer, Partner
from customers.views import scope_customers_to_user

User = get_user_model()

HEADER = "Full name,Email,Account balance\n"


def _csv(*rows, header=HEADER):
    return io.BytesIO((header + "".join(rows)).encode("utf-8"))


class ImportPartnerTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.acme = Partner.objects.create(name="Acme Fibre")
        self.rival = Partner.objects.create(name="Rival Networks")

        self.acme_staff = User.objects.create_user(
            username="acme-imports", password="pw-for-tests",
            role=User.Role.SUPPORT, allowed_partners=[self.acme.id],
        )
        self.both_staff = User.objects.create_user(
            username="two-partners", password="pw-for-tests",
            role=User.Role.SUPPORT, allowed_partners=[self.acme.id, self.rival.id],
        )
        self.hq_staff = User.objects.create_user(
            username="hq-imports", password="pw-for-tests", role=User.Role.SUPPORT
        )

    def _commit(self, user, body, **extra):
        self.client.force_authenticate(user)
        return self.client.post(
            "/api/customers/import-commit/", {"file": body, **extra}, format="multipart"
        )

    # ---- the fallback that closes the leak ------------------------------

    def test_a_single_partner_staff_members_import_lands_on_their_partner(self):
        """The commonest real case: a reseller's own staff importing their
        own list. It is the only reseller they could mean."""
        res = self._commit(self.acme_staff, _csv("Thabo Nkosi,t@example.com,1 250\n"))
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["created"], 1)
        customer = Customer.objects.get(full_name="Thabo Nkosi")
        self.assertEqual(customer.partner_id, self.acme.id)

    def test_an_imported_customer_is_not_visible_to_another_reseller(self):
        """The point of all of it."""
        self._commit(self.acme_staff, _csv("Thabo Nkosi,t@example.com,1 250\n"))
        rival_staff = User.objects.create_user(
            username="rival-staff", password="pw-for-tests",
            role=User.Role.SUPPORT, allowed_partners=[self.rival.id],
        )
        visible = scope_customers_to_user(Customer.objects.all(), rival_staff)
        self.assertFalse(visible.filter(full_name="Thabo Nkosi").exists())

    # ---- explicit ways to say which -------------------------------------

    def test_a_partner_column_in_the_file_is_used(self):
        res = self._commit(
            self.both_staff,
            _csv(
                "Naledi Dube,n@example.com,0,Rival Networks\n",
                header="Full name,Email,Account balance,Partner\n",
            ),
        )
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(
            Customer.objects.get(full_name="Naledi Dube").partner_id, self.rival.id
        )

    def test_a_partner_field_on_the_request_is_used(self):
        res = self._commit(
            self.both_staff, _csv("Sipho Molefe,s@example.com,0\n"), partner=str(self.rival.id)
        )
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(
            Customer.objects.get(full_name="Sipho Molefe").partner_id, self.rival.id
        )

    def test_an_unknown_partner_name_is_reported(self):
        res = self._commit(
            self.hq_staff,
            _csv(
                "Lost Lerato,l@example.com,0,Nonexistent Reseller\n",
                header="Full name,Email,Account balance,Partner\n",
            ),
        )
        self.assertEqual(res.data["created"], 0)
        self.assertIn("No partner found", str(res.data["skipped"]))

    # ---- and it cannot be used to cross a boundary ----------------------

    def test_restricted_staff_cannot_import_for_another_reseller(self):
        res = self._commit(
            self.acme_staff,
            _csv(
                "Sneaky Sam,s@example.com,0,Rival Networks\n",
                header="Full name,Email,Account balance,Partner\n",
            ),
        )
        self.assertEqual(res.data["created"], 0)
        self.assertIn("not one of the resellers", str(res.data["skipped"]))
        self.assertFalse(Customer.objects.filter(full_name="Sneaky Sam").exists())

    def test_multi_partner_staff_must_say_which_rather_than_importing_unowned(self):
        """Silence used to mean partner-less, which meant visible to
        everyone. Now it is refused with an explanation."""
        res = self._commit(self.both_staff, _csv("Ambiguous Ama,a@example.com,0\n"))
        self.assertEqual(res.data["created"], 0)
        self.assertIn("needs a reseller", str(res.data["skipped"]))

    # ---- unrestricted staff keep the direct-customer case ---------------

    def test_unrestricted_staff_may_still_import_direct_customers(self):
        """A customer with no reseller is a legitimate thing -- a direct
        customer. Only unrestricted staff can create one."""
        res = self._commit(self.hq_staff, _csv("Direct Dineo,d@example.com,0\n"))
        self.assertEqual(res.status_code, 200, res.data)
        self.assertIsNone(Customer.objects.get(full_name="Direct Dineo").partner_id)


class ImportMoneyAndAliasTests(TestCase):
    """The decimal fix and the missing aliases, through the real endpoint."""

    def setUp(self):
        self.client = APIClient()
        self.staff = User.objects.create_user(
            username="importer", password="pw-for-tests", role=User.Role.SUPPORT
        )
        self.client.force_authenticate(self.staff)

    def _commit(self, body):
        return self.client.post(
            "/api/customers/import-commit/", {"file": body}, format="multipart"
        )

    def test_a_comma_decimal_balance_is_not_multiplied_by_a_hundred(self):
        """The cell is quoted because it contains a comma -- which is
        exactly why a comma-decimal export is easy to get wrong and easy
        to miss."""
        from decimal import Decimal

        res = self._commit(_csv('Comma Cathy,c@example.com,"1 847,50"\n'))
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(
            Customer.objects.get(full_name="Comma Cathy").balance, Decimal("1847.50")
        )

    def test_a_space_thousands_separator_is_unambiguous(self):
        """A space can only be a thousands separator, so this needs no
        disambiguation -- unlike the comma case below."""
        from decimal import Decimal

        res = self._commit(_csv("Spaced Sana,s@example.com,1 500\n"))
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(
            Customer.objects.get(full_name="Spaced Sana").balance, Decimal("1500")
        )

    def test_title_case_headers_for_email_and_phone_are_picked_up(self):
        """These four fields were the only ones with no aliases, so a
        legacy export's headers were silently discarded."""
        res = self._commit(
            _csv(
                "Aliased Ayanda,a@example.com,0721234567,Acme Ltd,7700\n",
                header="Full name,Email,Phone,Company,Postal code\n",
            )
        )
        self.assertEqual(res.status_code, 200, res.data)
        customer = Customer.objects.get(full_name="Aliased Ayanda")
        self.assertEqual(customer.email, "a@example.com")
        self.assertEqual(customer.phone, "0721234567")
        self.assertEqual(customer.company_name, "Acme Ltd")
        self.assertEqual(customer.zip_code, "7700")

    def test_an_ambiguous_balance_is_reported_rather_than_guessed(self):
        """`1,500` is 1500 in en-US and 1.5 in en-ZA. Nothing in the cell
        says which, so it is refused rather than resolved by coin flip."""
        res = self._commit(_csv('Ambiguous Andile,a@example.com,"1,500"\n'))
        self.assertEqual(res.data["created"], 0)
        self.assertIn("could be", str(res.data["skipped"]))

    def test_a_failing_row_does_not_commit_the_rows_before_it(self):
        """The import is one transaction now, so the response and the
        database agree. A row that fails validation is still just skipped."""
        res = self._commit(
            _csv(
                "First Fine,f@example.com,10.00\n",
                "Second Bad,s@example.com,not-a-number\n",
                "Third Fine,t@example.com,20.00\n",
            )
        )
        self.assertEqual(res.data["created"], 2)
        self.assertEqual(res.data["skipped_count"], 1)
        self.assertTrue(Customer.objects.filter(full_name="First Fine").exists())
        self.assertTrue(Customer.objects.filter(full_name="Third Fine").exists())
        self.assertFalse(Customer.objects.filter(full_name="Second Bad").exists())
