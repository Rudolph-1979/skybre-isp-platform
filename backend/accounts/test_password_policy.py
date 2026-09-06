"""The password rules a staff login actually has to clear.

AUTH_PASSWORD_VALIDATORS lists four rules, but only one code path ran them:
PasswordResetConfirmView, i.e. the public "forgot password" screen. The path
an admin uses to CREATE a staff account, and to reset a colleague's password,
went through StaffAccountsSerializer instead -- which checked length and
nothing else. So "password123" and "48291736450" were refused from the
forgot-password screen and accepted for a brand-new login that can suspend a
customer's line, edit an invoice, or read the bank feed.

Each test below picks a password that trips exactly one rule and clears the
other three, so a failure names the rule that broke.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.test import TestCase
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.views import PasswordResetConfirmView, StaffAccountsViewSet

User = get_user_model()

# Clears all four rules: 15 characters, not on the common list, not numeric,
# and nothing like the username/name/email used below.
STRONG = "Tr0ut-Ladder-Ea"


class StaffAccountPasswordPolicyTests(TestCase):
    def setUp(self):
        # create_user() sets the password directly and deliberately bypasses
        # the validators -- that is Django's behaviour for programmatic
        # creation, and it is what the demo seeder and these fixtures rely on.
        # The rules under test guard the API path, not this one.
        self.admin = User.objects.create_user(username="boss", password="x", role="admin")
        self.factory = APIRequestFactory()

    def _create(self, **payload):
        body = {"username": "newbie", "first_name": "New", "last_name": "Bie",
                "email": "newbie@example.com", "role": "support", "is_active": True}
        body.update(payload)
        request = self.factory.post("/x", body, format="json")
        force_authenticate(request, user=self.admin)
        return StaffAccountsViewSet.as_view({"post": "create"})(request)

    def _patch(self, user, **payload):
        request = self.factory.patch("/x", payload, format="json")
        force_authenticate(request, user=self.admin)
        return StaffAccountsViewSet.as_view({"patch": "partial_update"})(request, pk=user.pk)

    # --- one test per rule ---------------------------------------------

    def test_an_entirely_numeric_password_is_refused(self):
        """An 11-digit password is long enough and not on the common list, so
        length alone lets it through. It is still a phone number."""
        response = self._create(password="48291736450")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("password", response.data)
        self.assertFalse(User.objects.filter(username="newbie").exists(),
                         "the account must not be created when its password is refused")

    def test_a_common_password_is_refused(self):
        response = self._create(password="password123")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("password", response.data)

    def test_a_password_that_is_just_the_username_is_refused(self):
        response = self._create(username="alexandra.smith", password="alexandra.smith")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("password", response.data)

    def test_a_nine_character_password_is_refused(self):
        """The minimum is 10. This one is otherwise strong -- mixed case,
        digit, punctuation -- so only the length rule can reject it."""
        response = self._create(password="Ab3!xY9zQ")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("password", response.data)

    def test_the_message_says_what_is_wrong_with_it(self):
        """The admin typing it needs to know which rule they hit, not just
        that the save failed."""
        response = self._create(password="password123")
        self.assertIn("too common", " ".join(str(m) for m in response.data["password"]).lower())

    # --- the accepted case, and that it really works ---------------------

    def test_a_strong_password_is_accepted_and_can_be_signed_in_with(self):
        response = self._create(password=STRONG)
        self.assertEqual(response.status_code, 201, response.data)
        user = User.objects.get(username="newbie")
        self.assertTrue(user.check_password(STRONG))

    def test_creating_without_a_password_still_sends_an_invite_instead(self):
        """The rules must not have broken the invite path, which deliberately
        creates an account with no usable password at all."""
        response = self._create()
        self.assertEqual(response.status_code, 201, response.data)
        self.assertFalse(User.objects.get(username="newbie").has_usable_password())

    # --- the edit path, not just create ---------------------------------

    def test_an_admin_cannot_reset_a_colleagues_password_to_a_weak_one(self):
        user = User.objects.create_user(username="tech", password=STRONG, role="support")
        response = self._patch(user, password="password123")
        self.assertEqual(response.status_code, 400, response.data)
        user.refresh_from_db()
        self.assertTrue(user.check_password(STRONG),
                        "a refused password must leave the old one in place")

    def test_an_admin_can_still_reset_it_to_a_strong_one(self):
        user = User.objects.create_user(username="tech", password="x", role="support")
        response = self._patch(user, password=STRONG)
        self.assertEqual(response.status_code, 200, response.data)
        user.refresh_from_db()
        self.assertTrue(user.check_password(STRONG))

    def test_editing_other_details_does_not_require_a_password(self):
        """password is write-only and optional; an edit that omits it must
        leave the existing one alone rather than tripping the rules."""
        user = User.objects.create_user(username="tech", password=STRONG, role="support")
        response = self._patch(user, first_name="Thabo")
        self.assertEqual(response.status_code, 200, response.data)
        user.refresh_from_db()
        self.assertEqual(user.first_name, "Thabo")
        self.assertTrue(user.check_password(STRONG))


class ForgotPasswordScreenPolicyTests(TestCase):
    """The path that always validated -- guarding that it picks up the raised
    minimum, since both paths now read the same setting."""

    def setUp(self):
        self.user = User.objects.create_user(username="tech", password="x", role="support")
        self.factory = APIRequestFactory()

    def _confirm(self, new_password):
        request = self.factory.post("/x", {
            "uid": urlsafe_base64_encode(force_bytes(self.user.pk)),
            "token": default_token_generator.make_token(self.user),
            "new_password": new_password,
        }, format="json")
        return PasswordResetConfirmView.as_view()(request)

    def test_a_nine_character_password_is_refused_here_too(self):
        response = self._confirm("Ab3!xY9zQ")
        self.assertEqual(response.status_code, 400, response.data)
        self.user.refresh_from_db()
        self.assertFalse(self.user.check_password("Ab3!xY9zQ"))

    def test_a_ten_character_strong_password_is_accepted(self):
        response = self._confirm(STRONG)
        self.assertEqual(response.status_code, 200, response.data)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(STRONG))


class TheMinimumIsExactlyTenTests(TestCase):
    """Pinning the boundary itself.

    Every password in the file above is 11-15 characters and the only
    length assertion is a 9-character refusal, so the suite pinned the
    minimum to "somewhere in 10..15" -- tightening the setting to 12 would
    have left it entirely green while every admin creating an account and
    every new hire following an invite hit a wall.
    """

    def setUp(self):
        self.user = User.objects.create_user(username="tech", password="x", role="support")
        self.factory = APIRequestFactory()

    def _confirm(self, new_password):
        request = self.factory.post("/x", {
            "uid": urlsafe_base64_encode(force_bytes(self.user.pk)),
            "token": default_token_generator.make_token(self.user),
            "new_password": new_password,
        }, format="json")
        return PasswordResetConfirmView.as_view()(request)

    def test_exactly_ten_characters_is_accepted(self):
        """'Vlei-Otter' is exactly 10 and clears the other three rules, so
        only the length boundary can decide it."""
        self.assertEqual(len("Vlei-Otter"), 10)
        response = self._confirm("Vlei-Otter")
        self.assertEqual(response.status_code, 200, response.data)

    def test_exactly_nine_characters_is_refused(self):
        self.assertEqual(len("Vlei-Otte"), 9)
        response = self._confirm("Vlei-Otte")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("10 characters", " ".join(str(m) for m in response.data["new_password"]))

