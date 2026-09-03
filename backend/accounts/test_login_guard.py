"""The login endpoint has to stop answering eventually.

/api/token/ had no throttle, no lockout and no delay, so staff passwords
could be guessed at whatever rate the server would answer -- against a
6-character minimum -- and so could the 6-digit TOTP code, which has
three values valid at any moment (valid_window=1).

The counter comes out of the audit log rather than Django's cache: no
CACHES is configured, so DRF's throttles fall back to per-process
LocMemCache, which with `gunicorn --workers 3` multiplies every limit by
three and resets it on each deploy. See accounts.login_guard.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.login_guard import MAX_PER_USERNAME, WINDOW
from audit.models import AuditEvent

User = get_user_model()


class LoginGuardTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="target", password="the-real-password", role=User.Role.SUPPORT
        )

    def _attempt(self, username="target", password="wrong", **extra):
        return self.client.post(
            "/api/token/", {"username": username, "password": password}, format="json", **extra
        )

    def _seed_failures(self, count, username="target", ip="203.0.113.9", age=timedelta(0)):
        AuditEvent.objects.bulk_create([
            AuditEvent(
                actor=None, actor_label=username, action=AuditEvent.Action.LOGIN_FAILED,
                detail="Wrong username or password", ip_address=ip, user_agent="",
            )
            for _ in range(count)
        ])
        if age:
            AuditEvent.objects.filter(actor_label=username).update(created_at=timezone.now() - age)

    def test_a_wrong_password_is_still_just_a_401(self):
        res = self._attempt()
        self.assertEqual(res.status_code, 401)

    def test_the_account_locks_after_too_many_failures(self):
        self._seed_failures(MAX_PER_USERNAME)
        res = self._attempt()
        self.assertEqual(res.status_code, 429)

    def test_the_lock_holds_even_with_the_correct_password(self):
        """The check runs BEFORE the password is verified, so a locked-out
        attacker gets no signal that they have just found the right one."""
        self._seed_failures(MAX_PER_USERNAME)
        res = self._attempt(password="the-real-password")
        self.assertEqual(res.status_code, 429)

    def test_just_under_the_limit_still_authenticates(self):
        self._seed_failures(MAX_PER_USERNAME - 1)
        res = self._attempt(password="the-real-password")
        self.assertEqual(res.status_code, 200, res.data)

    def test_old_failures_do_not_count(self):
        """Rolling window, not a sticky lock -- otherwise anyone could keep
        a real staff member out of the platform by failing their login on
        purpose."""
        self._seed_failures(MAX_PER_USERNAME * 2, age=WINDOW + timedelta(minutes=5))
        res = self._attempt(password="the-real-password")
        self.assertEqual(res.status_code, 200, res.data)

    def test_a_lock_on_one_account_does_not_lock_another(self):
        other = User.objects.create_user(
            username="innocent", password="their-password", role=User.Role.SUPPORT
        )
        self._seed_failures(MAX_PER_USERNAME, username="target")
        res = self._attempt(username="innocent", password="their-password")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertTrue(other.is_active)

    def test_there_is_no_per_ip_limit_to_weaponise(self):
        """Deliberately removed. request._audit_ip prefers the left-most
        X-Forwarded-For, which the client sets -- so an attacker rotating
        it bypassed the limit entirely, while anybody could lock a whole
        office out by sending failures carrying that office's address.
        audit.middleware's docstring says that value is safe BECAUSE
        nothing authorises on it; the guard contradicted that.

        So: junk failures attributed to an address must not stop a
        legitimate sign-in from it."""
        for i in range(60):
            self._seed_failures(1, username=f"victim{i}", ip="41.0.0.7")
        res = self._attempt(password="the-real-password", HTTP_X_FORWARDED_FOR="41.0.0.7")
        self.assertEqual(res.status_code, 200, res.data)

    def test_rotating_the_forwarded_header_does_not_evade_the_username_limit(self):
        """The other half: the limit that remains is keyed on the account,
        which no header can change."""
        self._seed_failures(MAX_PER_USERNAME)
        res = self._attempt(HTTP_X_FORWARDED_FOR="203.0.113.99")
        self.assertEqual(res.status_code, 429)

    def test_the_two_factor_code_prompt_does_not_count_as_a_failure(self):
        """The defect this cost us: the SPA signs a 2FA user in with TWO
        posts, and the first writes a LOGIN_FAILED row reading "Password
        correct, 2FA code required". Counting it meant ten CORRECT
        sign-ins exhausted a limit of ten and locked the admin out of
        their own platform."""
        AuditEvent.objects.bulk_create([
            AuditEvent(
                actor=None, actor_label="target", action=AuditEvent.Action.LOGIN_FAILED,
                detail="Password correct, 2FA code required", ip_address="203.0.113.9",
                user_agent="",
            )
            for _ in range(MAX_PER_USERNAME * 3)
        ])
        res = self._attempt(password="the-real-password")
        self.assertEqual(res.status_code, 200, res.data)

    def test_a_rejected_two_factor_code_does_still_count(self):
        """A wrong code IS a guess -- that is the second factor being
        brute-forced, which is the thing worth stopping."""
        AuditEvent.objects.bulk_create([
            AuditEvent(
                actor=None, actor_label="target", action=AuditEvent.Action.LOGIN_FAILED,
                detail="Password correct, 2FA code rejected", ip_address="203.0.113.9",
                user_agent="",
            )
            for _ in range(MAX_PER_USERNAME)
        ])
        res = self._attempt(password="the-real-password")
        self.assertEqual(res.status_code, 429)

    def test_an_empty_username_is_not_counted_or_blocked(self):
        self._seed_failures(MAX_PER_USERNAME, username="(no username given)")
        res = self._attempt(username="", password="x")
        self.assertNotEqual(res.status_code, 429)

    def test_a_real_failure_writes_the_audit_row_the_guard_counts(self):
        """The guard and the audit trail read the same rows -- if
        record_login_failure ever stopped writing them, this catches it."""
        before = AuditEvent.objects.filter(action=AuditEvent.Action.LOGIN_FAILED).count()
        self._attempt()
        after = AuditEvent.objects.filter(action=AuditEvent.Action.LOGIN_FAILED).count()
        self.assertEqual(after, before + 1)
