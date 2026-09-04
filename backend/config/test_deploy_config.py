"""The settings that decide whether the container starts at all.

Three of these were found by trying to deploy rather than by reading the
code, and one of them was self-inflicted:

  * ALLOWED_HOSTS="*" and CORS_ALLOW_ALL_ORIGINS=True are refused when
    DEBUG=False. Correct, and worth a test so a future edit cannot
    quietly drop the guard.
  * The container healthcheck curls http://localhost:8000/api/health/,
    which sends `Host: localhost` -- and Django answers a Host outside
    ALLOWED_HOSTS with 400 before the view ever runs. With a properly
    locked-down ALLOWED_HOSTS the healthcheck therefore failed forever,
    the backend was never reported healthy, and because the frontend
    waits on that condition, NOTHING would have started. The loopback
    names are appended for exactly that, and these tests pin both halves:
    the probe hosts work, and an unrelated Host is still rejected.
"""
from django.test import Client, TestCase, override_settings


class HealthEndpointTests(TestCase):
    def test_it_reports_ok_when_the_database_is_reachable(self):
        res = Client().get("/api/health/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"status": "ok"})

    def test_it_needs_no_authentication(self):
        """Docker polls it before anything is logged in, so it cannot."""
        res = Client().get("/api/health/")
        self.assertEqual(res.status_code, 200)

    def test_it_leaks_nothing_beyond_a_status(self):
        """No version, no counts, no settings -- it is unauthenticated."""
        self.assertEqual(set(Client().get("/api/health/").json()), {"status"})


@override_settings(ALLOWED_HOSTS=["portal.skybre.co.za", "localhost", "127.0.0.1"])
class HealthcheckHostTests(TestCase):
    """What the compose healthcheck actually sends."""

    def test_the_loopback_probe_hosts_are_accepted(self):
        for host in ("localhost", "127.0.0.1"):
            with self.subTest(host=host):
                res = Client().get("/api/health/", HTTP_HOST=host)
                self.assertEqual(res.status_code, 200, f"healthcheck would fail for Host: {host}")

    def test_the_real_hostname_is_accepted(self):
        res = Client().get("/api/health/", HTTP_HOST="portal.skybre.co.za")
        self.assertEqual(res.status_code, 200)

    def test_an_unrelated_host_is_still_rejected(self):
        """Allowing the probe hosts must not re-open the setting."""
        res = Client().get("/api/health/", HTTP_HOST="evil.example.com")
        self.assertEqual(res.status_code, 400)


class BootGuardTests(TestCase):
    """The guards are import-time, so they are exercised by re-importing
    settings under a patched environment rather than by override_settings
    (which runs long after the module has loaded)."""

    def _load_settings(self, **env):
        import importlib
        import os
        from unittest import mock

        base = {
            "SECRET_KEY": "a-long-random-production-key-value-abcdefghijk",
            "DEBUG": "False",
            "ALLOWED_HOSTS": "portal.skybre.co.za",
            "CORS_ALLOW_ALL_ORIGINS": "False",
        }
        base.update(env)
        with mock.patch.dict(os.environ, base, clear=False):
            # decouple caches nothing across calls; a fresh import re-reads.
            module = importlib.import_module("config.settings")
            return importlib.reload(module)

    def test_a_wildcard_allowed_hosts_is_refused_when_debug_is_off(self):
        from django.core.exceptions import ImproperlyConfigured

        with self.assertRaises(ImproperlyConfigured) as caught:
            self._load_settings(ALLOWED_HOSTS="*")
        self.assertIn("ALLOWED_HOSTS", str(caught.exception))

    def test_wide_open_cors_is_refused_when_debug_is_off(self):
        from django.core.exceptions import ImproperlyConfigured

        with self.assertRaises(ImproperlyConfigured) as caught:
            self._load_settings(CORS_ALLOW_ALL_ORIGINS="True")
        self.assertIn("CORS_ALLOW_ALL_ORIGINS", str(caught.exception))

    def test_a_correct_production_config_loads(self):
        settings_module = self._load_settings()
        self.assertNotIn("*", settings_module.ALLOWED_HOSTS)
        self.assertFalse(settings_module.CORS_ALLOW_ALL_ORIGINS)

    def test_the_probe_hosts_are_appended_to_a_locked_down_list(self):
        """The regression that would have stopped the platform starting."""
        settings_module = self._load_settings(ALLOWED_HOSTS="portal.skybre.co.za")
        self.assertIn("localhost", settings_module.ALLOWED_HOSTS)
        self.assertIn("127.0.0.1", settings_module.ALLOWED_HOSTS)
        self.assertIn("portal.skybre.co.za", settings_module.ALLOWED_HOSTS)

    def test_local_development_is_unaffected(self):
        """The guards check only when DEBUG is False."""
        settings_module = self._load_settings(DEBUG="True", ALLOWED_HOSTS="*",
                                              CORS_ALLOW_ALL_ORIGINS="True")
        self.assertIn("*", settings_module.ALLOWED_HOSTS)

    def tearDown(self):
        # Leave the process holding the real settings, not the last patched
        # reload -- otherwise every test that runs after this file sees them.
        import importlib

        importlib.reload(importlib.import_module("config.settings"))
