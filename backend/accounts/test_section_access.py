"""Section access must fail closed.

This file exists because it did not. `allowed_sections` being empty meant
"unrestricted", and empty is what every new account starts with -- so the
restriction mechanism was opt-in and silently off for everybody nobody
had configured. On the live database that was nine of nine non-admin
staff: three technicians and a sales rep could open every salary in the
company, the VAT returns, and the store holding the SMTP, RADIUS and bank
API credentials.

Nothing was broken. The check did exactly what its comment said. The
default was backwards, and a default that fails open is the kind of bug
that never announces itself -- it just quietly stops being a control.

What these tests pin:

  * empty grants NOTHING, for every staff role,
  * Admin still bypasses, so this can never lock Admin out,
  * a granted section works and an ungranted one does not,
  * an account someone has explicitly configured is not widened, and
  * the specific hole: a technician cannot read a VAT return.
"""
import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.permissions import user_can_access_section

User = get_user_model()

START = datetime.date(2026, 5, 1)
END = datetime.date(2026, 6, 30)

# Every non-admin staff role on this platform. Parameterised rather than
# testing one, because the old rule was role-blind and so is the new one
# -- a fix that only closed the door for technicians would be worse than
# no fix, since it would look done.
STAFF_ROLES = ["technician", "support", "sales", "accounts", "management"]


class EmptyMeansNothingTests(TestCase):
    def _staff(self, role, sections=None, username=None):
        return User.objects.create_user(
            username=username or f"{role}_user", password="x", role=role,
            is_staff=True, allowed_sections=sections or [],
        )

    def test_an_unconfigured_staff_account_gets_no_sections(self):
        for role in STAFF_ROLES:
            with self.subTest(role=role):
                user = self._staff(role)

                self.assertFalse(user_can_access_section(user, "accountant"))
                self.assertFalse(user_can_access_section(user, "configs"))
                self.assertFalse(user_can_access_section(user, "finance"))

    def test_admin_still_passes_with_an_empty_list(self):
        """Admin bypasses on purpose: a restriction must never be able to
        lock the Admin account out of part of its own platform."""
        admin = User.objects.create_user(
            username="boss", password="x", role="admin", is_staff=True, allowed_sections=[],
        )

        self.assertTrue(user_can_access_section(admin, "accountant"))
        self.assertTrue(user_can_access_section(admin, "configs"))

    def test_a_granted_section_is_allowed(self):
        user = self._staff("accounts", ["finance", "accountant"])

        self.assertTrue(user_can_access_section(user, "accountant"))

    def test_an_ungranted_section_is_refused(self):
        user = self._staff("accounts", ["finance", "accountant"])

        self.assertFalse(user_can_access_section(user, "configs"))

    def test_an_anonymous_request_is_refused(self):
        from django.contrib.auth.models import AnonymousUser

        self.assertFalse(user_can_access_section(AnonymousUser(), "accountant"))

    def test_customers_are_not_governed_by_this_at_all(self):
        """Customer accounts go through the portal, which scopes what they
        see by its own logic. Running them through section checks would
        deny them their own portal."""
        customer = User.objects.create_user(
            username="cust", password="x", role="customer",
        )

        self.assertTrue(user_can_access_section(customer, "customers"))


class TheHoleItselfTests(TestCase):
    """The thing that actually went wrong, at the API, end to end."""

    def setUp(self):
        self.client = APIClient()
        self.tech = User.objects.create_user(
            username="fanie", password="x", role="technician", is_staff=True,
            allowed_sections=[],
        )

    def _as(self, user, path):
        self.client.force_authenticate(user)
        return self.client.get(path)

    def test_a_technician_cannot_read_a_vat_return(self):
        response = self._as(self.tech, f"/api/vat-return/?period_start={START}&period_end={END}")

        self.assertEqual(response.status_code, 403)

    def test_a_technician_cannot_download_the_vat_return(self):
        response = self._as(
            self.tech, f"/api/vat-return/export/?period_start={START}&period_end={END}"
        )

        self.assertEqual(response.status_code, 403)

    def test_a_technician_cannot_download_the_vat_pdf(self):
        response = self._as(
            self.tech, f"/api/vat-return/pdf/?period_start={START}&period_end={END}"
        )

        self.assertEqual(response.status_code, 403)

    def test_a_technician_cannot_list_expenses(self):
        self.assertEqual(self._as(self.tech, "/api/expenses/").status_code, 403)

    def test_an_accounts_user_still_can(self):
        """The other half. A fix that denied everybody would pass every
        test above and break the platform."""
        books = User.objects.create_user(
            username="janine", password="x", role="accounts", is_staff=True,
            allowed_sections=["finance", "accountant", "customers", "services", "inventory"],
        )

        response = self._as(books, f"/api/vat-return/?period_start={START}&period_end={END}")

        self.assertEqual(response.status_code, 200)

    def test_a_technician_keeps_the_sections_the_backfill_grants_them(self):
        """Technicians are meant to lose Accountant and Configs, not their
        own job. Vehicles and tickets are what they are on the platform
        for."""
        self.tech.allowed_sections = [
            "scheduling", "tickets", "customers", "services", "networking", "inventory", "vehicles",
        ]
        self.tech.save(update_fields=["allowed_sections"])

        self.assertTrue(user_can_access_section(self.tech, "vehicles"))
        self.assertTrue(user_can_access_section(self.tech, "tickets"))
        self.assertFalse(user_can_access_section(self.tech, "accountant"))
        self.assertFalse(user_can_access_section(self.tech, "configs"))


class BackfillTests(TestCase):
    """The migration that makes flipping the default safe to deploy."""

    def _backfill(self):
        from importlib import import_module
        import os
        import re

        from django.apps import apps as django_apps

        directory = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "accounts", "migrations"
        )
        names = sorted(
            name[:-3] for name in os.listdir(directory)
            if re.match(r"^\d{4}_.*backfill_allowed_sections\.py$", name)
        )
        module = import_module(f"accounts.migrations.{names[-1]}")
        module.backfill(django_apps, None)

    def test_it_gives_an_unconfigured_account_its_role_s_sections(self):
        tech = User.objects.create_user(
            username="zandre", password="x", role="technician", is_staff=True, allowed_sections=[],
        )

        self._backfill()

        tech.refresh_from_db()
        self.assertIn("vehicles", tech.allowed_sections)
        self.assertNotIn("accountant", tech.allowed_sections)
        self.assertNotIn("configs", tech.allowed_sections)

    def test_it_does_not_widen_an_account_somebody_already_configured(self):
        """Somebody chose that list. A migration quietly adding to it would
        be the same class of bug as the one being fixed."""
        narrowed = User.objects.create_user(
            username="anton", password="x", role="support", is_staff=True,
            allowed_sections=["tickets"],
        )

        self._backfill()

        narrowed.refresh_from_db()
        self.assertEqual(narrowed.allowed_sections, ["tickets"])

    def test_it_leaves_admin_accounts_empty(self):
        """Admin bypasses the check entirely, so writing a list for one
        would imply a restriction that does not exist."""
        admin = User.objects.create_user(
            username="rudolph", password="x", role="admin", is_staff=True, allowed_sections=[],
        )

        self._backfill()

        admin.refresh_from_db()
        self.assertEqual(admin.allowed_sections, [])
        self.assertTrue(user_can_access_section(admin, "configs"))

    def test_nobody_is_granted_configs(self):
        """It holds SMTP, RADIUS and bank API credentials. Admin reaches it
        by bypassing this check; nobody else should reach it at all."""
        for role in STAFF_ROLES:
            User.objects.create_user(
                username=f"{role}_x", password="x", role=role, is_staff=True, allowed_sections=[],
            )

        self._backfill()

        for user in User.objects.exclude(role="admin").exclude(role="customer"):
            with self.subTest(user=user.username):
                self.assertNotIn("configs", user.allowed_sections)

    def test_only_accounts_and_management_get_the_accountant_section(self):
        """The finding that started this: everyone could read every salary."""
        for role in STAFF_ROLES:
            User.objects.create_user(
                username=f"{role}_y", password="x", role=role, is_staff=True, allowed_sections=[],
            )

        self._backfill()

        with_accountant = {
            user.role for user in User.objects.exclude(role="admin").exclude(role="customer")
            if "accountant" in user.allowed_sections
        }

        self.assertEqual(with_accountant, {"accounts", "management"})
