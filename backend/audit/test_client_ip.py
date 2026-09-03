"""A forged X-Forwarded-For must not be able to erase the audit trail.

audit.middleware.client_ip took the leftmost X-Forwarded-For value
verbatim. Its docstring correctly argued the value is forgeable and that
this is fine, because nothing authorises anything based on it -- but
forgeable was never the problem. Unvalidated was.

AuditEvent.ip_address is a GenericIPAddressField over a Postgres `inet`
column. A header that is not an address at all reached the INSERT and was
rejected there, and both audit writers swallow every exception so that a
failure to log can never break the action being logged. So one junk
header made the sender's failed logins, successful logins and record
edits silently vanish from the trail that exists to be tamper-evident.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from audit.models import AuditEvent
from audit.middleware import client_ip

User = get_user_model()


class _FakeRequest:
    def __init__(self, **meta):
        self.META = meta


class ClientIpParsingTests(TestCase):
    def test_a_normal_forwarded_address_is_kept(self):
        self.assertEqual(
            client_ip(_FakeRequest(HTTP_X_FORWARDED_FOR="203.0.113.9", REMOTE_ADDR="127.0.0.1")),
            "203.0.113.9",
        )

    def test_the_leftmost_of_a_chain_is_used(self):
        self.assertEqual(
            client_ip(_FakeRequest(HTTP_X_FORWARDED_FOR="203.0.113.9, 10.0.0.1, 127.0.0.1")),
            "203.0.113.9",
        )

    def test_an_ipv6_address_is_kept(self):
        self.assertEqual(
            client_ip(_FakeRequest(HTTP_X_FORWARDED_FOR="2001:db8::1")), "2001:db8::1"
        )

    def test_junk_falls_back_to_remote_addr(self):
        self.assertEqual(
            client_ip(_FakeRequest(HTTP_X_FORWARDED_FOR="notanip", REMOTE_ADDR="127.0.0.1")),
            "127.0.0.1",
        )

    def test_a_sql_shaped_string_falls_back(self):
        self.assertEqual(
            client_ip(_FakeRequest(HTTP_X_FORWARDED_FOR="'); DROP TABLE audit_auditevent; --",
                                   REMOTE_ADDR="127.0.0.1")),
            "127.0.0.1",
        )

    def test_an_appended_port_is_stripped(self):
        self.assertEqual(
            client_ip(_FakeRequest(HTTP_X_FORWARDED_FOR="203.0.113.9:51520")), "203.0.113.9"
        )

    def test_a_bracketed_ipv6_address_is_unwrapped(self):
        self.assertEqual(client_ip(_FakeRequest(HTTP_X_FORWARDED_FOR="[2001:db8::1]")), "2001:db8::1")

    def test_junk_in_both_places_yields_none(self):
        self.assertIsNone(
            client_ip(_FakeRequest(HTTP_X_FORWARDED_FOR="notanip", REMOTE_ADDR="alsonotanip"))
        )

    def test_no_headers_at_all_yields_none(self):
        self.assertIsNone(client_ip(_FakeRequest()))


class ForgedHeaderDoesNotLoseTheRowTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        User.objects.create_user(
            username="target", password="the-real-password", role=User.Role.SUPPORT
        )

    def test_a_failed_login_is_still_recorded_with_a_junk_header(self):
        """The end-to-end version: the row has to survive."""
        before = AuditEvent.objects.filter(action=AuditEvent.Action.LOGIN_FAILED).count()
        res = self.client.post(
            "/api/token/", {"username": "target", "password": "wrong"},
            format="json", HTTP_X_FORWARDED_FOR="notanip",
        )
        self.assertEqual(res.status_code, 401)
        after = AuditEvent.objects.filter(action=AuditEvent.Action.LOGIN_FAILED).count()
        self.assertEqual(after, before + 1)

    def test_a_successful_login_is_still_recorded_with_a_junk_header(self):
        before = AuditEvent.objects.filter(action=AuditEvent.Action.LOGIN).count()
        res = self.client.post(
            "/api/token/", {"username": "target", "password": "the-real-password"},
            format="json", HTTP_X_FORWARDED_FOR="notanip",
        )
        self.assertEqual(res.status_code, 200, res.data)
        after = AuditEvent.objects.filter(action=AuditEvent.Action.LOGIN).count()
        self.assertEqual(after, before + 1)
