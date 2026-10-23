"""Staff codes, and what Conecto is told about them.

Conecto will not let a staff member register in the app unless it knows
their code and the role it carries. That makes three moments matter, and
they are not equally forgiving:

  * CREATION. Get it wrong and somebody cannot use the app. Annoying,
    obvious, fixed in a minute.
  * ROLE CHANGE. Get it wrong and somebody has powers they should not, or
    lacks ones they need. Noticed eventually.
  * DEACTIVATION. Get it wrong and a person who has left the company
    keeps a working field app, and nothing anywhere says so. Nobody
    notices, because the only signal would be someone looking for it.

Most of what follows is about the third one. The rest is about not
sending Conecto things it should never hear about -- customers, and every
unrelated save that happens to touch a staff row.

Every push here goes through transaction.on_commit, and Django's TestCase
wraps each test in a transaction that is rolled back rather than
committed -- so without captureOnCommitCallbacks nothing fires at all.
That matters most for the tests asserting nothing was SENT: they would
pass on an empty mock while the real code sent anything it liked. Hence
the wrapper on the negative cases too, where it looks redundant.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from customers.models import Customer

from .mapping import staff_role_to_conecto
from .models import ConectoCall

User = get_user_model()

TOKEN = "test-shared-secret"

URLS = {
    "registerStaffCode": "https://example.invalid/registerStaffCode",
}

CONFIGURED = override_settings(
    CONECTO_API_KEY=TOKEN, CONECTO_WEBHOOK_TOKEN=TOKEN, CONECTO_URLS=URLS
)


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._payload


class CodeAssignmentTests(TestCase):
    """The code itself, before anybody is told about it."""

    def test_a_staff_account_gets_a_code(self):
        user = User.objects.create_user(username="fanie", password="x", role="technician")

        self.assertTrue(user.staff_code)
        self.assertEqual(user.staff_code, f"STF-{user.pk:06d}")

    def test_a_customer_account_does_not(self):
        """Customers have customer_id. A customer with a staff code could
        register in a field app."""
        user = User.objects.create_user(username="buyer", password="x", role="customer")

        self.assertIsNone(user.staff_code)

    def test_the_code_never_changes(self):
        """Conecto keys registration on it. A code that moved would strand
        whoever had already registered with the old one."""
        user = User.objects.create_user(username="liesi", password="x", role="support")
        original = user.staff_code

        user.first_name = "Liesi"
        user.role = "management"
        user.save()

        user.refresh_from_db()
        self.assertEqual(user.staff_code, original)

    def test_promoting_a_customer_issues_one(self):
        user = User.objects.create_user(username="newbie", password="x", role="customer")
        self.assertIsNone(user.staff_code)

        user.role = "technician"
        user.save()

        user.refresh_from_db()
        self.assertTrue(user.staff_code)

    def test_two_staff_never_share_a_code(self):
        codes = {
            User.objects.create_user(username=f"s{i}", password="x", role="support").staff_code
            for i in range(5)
        }

        self.assertEqual(len(codes), 5)


class RoleMappingTests(TestCase):
    """The agreed translation. Written out one role at a time rather than
    looped, so changing one of them has to be a deliberate edit to a line
    that names it."""

    def test_admin_is_admin(self):
        self.assertEqual(staff_role_to_conecto("admin"), "admin")

    def test_management_is_admin(self):
        self.assertEqual(staff_role_to_conecto("management"), "admin")

    def test_support_is_staff(self):
        self.assertEqual(staff_role_to_conecto("support"), "staff")

    def test_sales_is_staff(self):
        self.assertEqual(staff_role_to_conecto("sales"), "staff")

    def test_accounts_is_staff(self):
        self.assertEqual(staff_role_to_conecto("accounts"), "staff")

    def test_technician_is_technician(self):
        self.assertEqual(staff_role_to_conecto("technician"), "technician")

    def test_an_unmapped_role_gets_the_least_privilege(self):
        """A role added later must not silently become an admin in the app."""
        self.assertEqual(staff_role_to_conecto("something_new"), "staff")


@CONFIGURED
class PushTests(TestCase):
    def _sent(self, request):
        """The payloads actually sent, in order."""
        return [call.kwargs["json"] for call in request.call_args_list]

    def test_creating_a_staff_account_sends_the_code_and_role(self):
        with patch("conecto.client.requests.request") as request:
            request.return_value = FakeResponse(200, {"success": True})
            with self.captureOnCommitCallbacks(execute=True):
                user = User.objects.create_user(username="fanie", password="x", role="technician")

        sent = self._sent(request)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["staff_code"], user.staff_code)
        self.assertEqual(sent[0]["role"], "technician")
        self.assertTrue(sent[0]["is_active"])

    def test_creating_a_customer_sends_nothing(self):
        with patch("conecto.client.requests.request") as request:
            with self.captureOnCommitCallbacks(execute=True):
                User.objects.create_user(username="buyer", password="x", role="customer")

        request.assert_not_called()

    def test_a_role_change_is_pushed(self):
        user = User.objects.create_user(username="liesi", password="x", role="support")

        with patch("conecto.client.requests.request") as request:
            request.return_value = FakeResponse(200, {"success": True})
            with self.captureOnCommitCallbacks(execute=True):
                user.role = "management"
                user.save()

        sent = self._sent(request)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["role"], "admin")

    def test_deactivating_revokes(self):
        """The one that matters. Somebody who leaves loses the app from the
        same action that switches off their login here -- not from a second
        step in another system that eventually does not happen."""
        user = User.objects.create_user(username="gone", password="x", role="technician")

        with patch("conecto.client.requests.request") as request:
            request.return_value = FakeResponse(200, {"success": True})
            with self.captureOnCommitCallbacks(execute=True):
                user.is_active = False
                user.save()

        sent = self._sent(request)
        self.assertEqual(len(sent), 1)
        self.assertFalse(sent[0]["is_active"])
        self.assertEqual(sent[0]["staff_code"], user.staff_code)

    def test_reactivating_restores(self):
        user = User.objects.create_user(username="back", password="x", role="support")
        user.is_active = False
        user.save()

        with patch("conecto.client.requests.request") as request:
            request.return_value = FakeResponse(200, {"success": True})
            with self.captureOnCommitCallbacks(execute=True):
                user.is_active = True
                user.save()

        self.assertTrue(self._sent(request)[0]["is_active"])

    def test_demoting_to_customer_revokes(self):
        """They keep the row and the code, but they are not staff any more
        and must not keep a staff app."""
        user = User.objects.create_user(username="moved", password="x", role="support")

        with patch("conecto.client.requests.request") as request:
            request.return_value = FakeResponse(200, {"success": True})
            with self.captureOnCommitCallbacks(execute=True):
                user.role = "customer"
                user.save()

        sent = self._sent(request)
        self.assertEqual(len(sent), 1)
        self.assertFalse(sent[0]["is_active"])

    def test_an_unrelated_save_sends_nothing(self):
        """Staff rows are written by logins, permission edits and password
        resets. Re-sending an unchanged role on each of those is a call
        that can only do harm if it lands out of order."""
        user = User.objects.create_user(username="busy", password="x", role="support")

        with patch("conecto.client.requests.request") as request:
            with self.captureOnCommitCallbacks(execute=True):
                user.phone = "0821234567"
                user.first_name = "Busy"
                user.save()

        request.assert_not_called()

    def test_a_failed_push_leaves_a_replayable_row(self):
        """Conecto retries nothing. A staff member whose code never arrived
        cannot register, and the only way to find them later is this row."""
        with patch("conecto.client.requests.request") as request:
            request.return_value = FakeResponse(500, text="upstream error")
            with self.captureOnCommitCallbacks(execute=True):
                user = User.objects.create_user(username="unlucky", password="x", role="support")

        row = ConectoCall.objects.get(endpoint="registerStaffCode")
        self.assertEqual(row.status, ConectoCall.Status.FAILED)
        self.assertEqual(row.subject_ref, user.staff_code)

    @override_settings(CONECTO_URLS={})
    def test_nothing_is_sent_before_conecto_publishes_the_endpoint(self):
        """This ships before their side exists. An unconfigured URL must be
        a quiet no-op, not an error on every staff account created."""
        with patch("conecto.client.requests.request") as request:
            with self.captureOnCommitCallbacks(execute=True):
                user = User.objects.create_user(username="early", password="x", role="support")

        request.assert_not_called()
        self.assertTrue(user.staff_code)


@CONFIGURED
class CustomerSeparationTests(TestCase):
    """Customers and staff share one user table. Nothing here may blur
    them."""

    def test_a_customer_user_beside_a_customer_record_is_still_not_staff(self):
        Customer.objects.create(customer_id="CUS-000001", full_name="Jan Botha")
        user = User.objects.create_user(username="jan", password="x", role="customer")

        with patch("conecto.client.requests.request") as request:
            with self.captureOnCommitCallbacks(execute=True):
                user.first_name = "Jan"
                user.save()

        request.assert_not_called()
        self.assertIsNone(user.staff_code)
