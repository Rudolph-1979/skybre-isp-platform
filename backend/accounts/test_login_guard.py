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

from accounts.login_guard import MAX_PER_IP, MAX_PER_USERNAME, WINDOW
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

    def test_credential_stuffing_from_one_address_is_capped(self):
        """The per-IP limit catches a host working through a list of
        accounts, where no single username reaches its own limit."""
        for i in range(MAX_PER_IP):
            self._seed_failures(1, username=f"victim{i}", ip="198.51.100.7")
        res = self._attempt(username="target", password="the-real-password",
                            HTTP_X_FORWARDED_FOR="198.51.100.7")
        self.assertEqual(res.status_code, 429)

    def test_a_real_failure_writes_the_audit_row_the_guard_counts(self):
        """The guard and the audit trail read the same rows -- if
        record_login_failure ever stopped writing them, this catches it."""
        before = AuditEvent.objects.filter(action=AuditEvent.Action.LOGIN_FAILED).count()
        self._attempt()
        after = AuditEvent.objects.filter(action=AuditEvent.Action.LOGIN_FAILED).count()
        self.assertEqual(after, before + 1)
