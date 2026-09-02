"""Inviting a new staff member instead of inventing a password for them.

Creating an account used to REQUIRE a password, which meant the admin chose it
and then had to get it to the person somehow -- read out, messaged, written
down. Every one of those is a password picked by the wrong person and left
somewhere it shouldn't be.
"""
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.mail import get_connection
from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.views import StaffAccountsViewSet

User = get_user_model()


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class StaffInviteTests(TestCase):
    def setUp(self):
        # The platform builds its own mail connection from EmailSettings
        # rather than using settings.EMAIL_BACKEND, and with no SMTP host
        # configured it falls back to the CONSOLE backend -- which prints and
        # never touches mail.outbox. Correct behaviour; it just means
        # override_settings alone can't see the mail.
        patcher = mock.patch(
            "accounts.password_reset.get_email_connection",
            lambda: get_connection(backend="django.core.mail.backends.locmem.EmailBackend"),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        self.admin = User.objects.create_user(
            username="boss", password="x", role="admin", first_name="Rudolph", last_name="Grunder"
        )
        self.factory = APIRequestFactory()
        mail.outbox = []

    def _create(self, **payload):
        body = {"username": "newbie", "first_name": "New", "last_name": "Bie",
                "email": "newbie@example.com", "role": "support", "is_active": True}
        body.update(payload)
        request = self.factory.post("/x", body, format="json")
        force_authenticate(request, user=self.admin)
        return StaffAccountsViewSet.as_view({"post": "create"})(request)

    def test_creating_without_a_password_sends_an_invite(self):
        response = self._create()
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.data["invite"]["sent"])
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("newbie@example.com", mail.outbox[0].to)

    def test_the_invite_names_the_username_and_carries_a_link(self):
        self._create()
        body = mail.outbox[0].body
        self.assertIn("newbie", body)
        self.assertIn("/reset-password/", body)

    def test_it_says_who_invited_them(self):
        self._create()
        self.assertIn("Rudolph Grunder", mail.outbox[0].body)

    def test_no_password_means_the_account_cannot_be_signed_into_yet(self):
        self._create()
        user = User.objects.get(username="newbie")
        self.assertFalse(user.has_usable_password(),
                         "an invited account must not be reachable before they set a password")

    def test_the_invite_link_actually_works(self):
        """End to end: the token in the email sets a password on an account
        that had none."""
        from django.contrib.auth.tokens import default_token_generator
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode

        from accounts.views import PasswordResetConfirmView

        self._create()
        user = User.objects.get(username="newbie")
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)

        request = self.factory.post(
            "/x", {"uid": uid, "token": token, "new_password": "a-Real-Password-9"}, format="json"
        )
        response = PasswordResetConfirmView.as_view()(request)
        self.assertEqual(response.status_code, 200, response.data)
        user.refresh_from_db()
        self.assertTrue(user.has_usable_password())
        self.assertTrue(user.check_password("a-Real-Password-9"))

    def test_a_password_can_still_be_set_directly(self):
        response = self._create(password="chosen-by-admin-1")
        self.assertEqual(response.status_code, 201)
        user = User.objects.get(username="newbie")
        self.assertTrue(user.check_password("chosen-by-admin-1"))

    def test_neither_a_password_nor_an_email_is_refused(self):
        response = self._create(email="")
        self.assertEqual(response.status_code, 400)
        self.assertIn("email", response.data)

    def test_a_failed_send_does_not_lose_the_account(self):
        """An SMTP outage must not throw away an account that was correctly
        created -- and the admin must be told, not left assuming."""
        with mock.patch("accounts.views.send_staff_invite_email", side_effect=RuntimeError("smtp down")):
            response = self._create()
        self.assertEqual(response.status_code, 201)
        self.assertTrue(User.objects.filter(username="newbie").exists())
        self.assertFalse(response.data["invite"]["sent"])
        self.assertIn("could not be sent", response.data["invite"]["detail"])

    # ---- the per-row button ---------------------------------------------

    def _send_invite(self, user, actor=None):
        request = self.factory.post("/x")
        force_authenticate(request, user=actor or self.admin)
        return StaffAccountsViewSet.as_view({"post": "send_invite"})(request, pk=user.pk)

    def test_the_invite_can_be_re_sent(self):
        self._create()
        mail.outbox = []
        user = User.objects.get(username="newbie")
        response = self._send_invite(user)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)

    def test_re_sending_needs_an_email_address(self):
        user = User.objects.create_user(username="noemail", password="x", role="support")
        response = self._send_invite(user)
        self.assertEqual(response.status_code, 400)
        self.assertIn("no email address", response.data["detail"])
