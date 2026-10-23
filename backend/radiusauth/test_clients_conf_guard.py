"""The guard that stands between an empty render and a dark network, and
the audit trail that says who caused one.

Both exist because of 2026-09-20. A render of clients.conf came back with
no clients in it while all seven NAS rows sat in the database, active. The
host installed it -- an empty clients.conf is perfectly valid -- restarted
FreeRADIUS onto it, and from that moment every router on the network was
an unknown client. Unknown clients are not rejected, they are ignored, so
routers retransmitted into silence, accounting stopped, and every customer
read as offline for ten hours while `systemctl status` said healthy.

Twelve later attempts to install the real config were rejected and rolled
back to that empty file, because "the previous config" was assumed to be a
good one.

The tests below are mostly about the word "valid" not meaning "correct",
and about the fact that nothing in the platform could say who had changed
those rows.
"""
import datetime
import json
import os
import tempfile

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.testing import ALL_SECTIONS
from radiusauth.clients_conf import (
    clients_conf_state,
    count_client_blocks,
    render_clients_conf_text,
    write_clients_conf_spool,
)
from radiusauth.models import RadiusNasClient, RadiusNasClientAudit

User = get_user_model()


def make_client(**overrides):
    values = {
        "name": "Skybre-PPP-Teraco-JHB",
        "ip_address": "102.23.159.30",
        "shortname": "teraco-jhb",
        "secret": "a-shared-secret",
        "realm": "jhb",
    }
    values.update(overrides)
    return RadiusNasClient.objects.create(**values)


class SpoolDirTestCase(TestCase):
    """Points the spool at a throwaway directory for the duration."""

    def setUp(self):
        self.spool_dir = tempfile.mkdtemp(prefix="skybre-spool-test-")
        self.spool = os.path.join(self.spool_dir, "clients.conf")
        # RADIUS_CLIENTS_SPOOL_TEST_OK is the opt-in described in
        # write_clients_conf_spool: without it, nothing running against a
        # test database is allowed to write a spool file at all. These
        # tests are the ones that genuinely mean to, and they write into a
        # throwaway directory of their own.
        self.settings_patch = override_settings(
            RADIUS_CLIENTS_SPOOL=self.spool, RADIUS_CLIENTS_SPOOL_TEST_OK=True,
        )
        self.settings_patch.enable()
        self.addCleanup(self.settings_patch.disable)

    def write_status(self, **payload):
        with open(os.path.join(self.spool_dir, "apply_status.json"), "w") as f:
            json.dump(payload, f)


class CountingTests(TestCase):
    def test_it_counts_what_the_renderer_actually_produces(self):
        """Counting is done on rendered text, so this has to agree with the
        renderer rather than with an idea of what the renderer emits."""
        make_client()
        make_client(name="KLB PE/GNE", ip_address="10.200.0.8", shortname="KLB-PE", realm="")

        self.assertEqual(count_client_blocks(render_clients_conf_text()), 2)

    def test_the_empty_render_counts_zero_rather_than_looking_empty(self):
        """The file that caused the outage was 212 bytes of comments. Size,
        emptiness and truthiness all read as "not empty"; only the client
        count tells the truth about it."""
        rendered = render_clients_conf_text()

        self.assertNotEqual(rendered.strip(), "")
        self.assertEqual(count_client_blocks(rendered), 0)


class SpoolGuardTests(SpoolDirTestCase):
    def test_it_refuses_to_write_no_clients_over_existing_clients(self):
        """The outage, in one test. The spool holds a working config, the
        render comes back empty, and the empty one must not reach disk."""
        make_client()
        write_clients_conf_spool(reason="initial")
        before = open(self.spool).read()
        self.assertEqual(count_client_blocks(before), 1)

        RadiusNasClient.objects.update(is_active=False)
        result = write_clients_conf_spool(reason="everything went inactive")

        self.assertIsNone(result)
        self.assertEqual(open(self.spool).read(), before)

    def test_it_writes_an_empty_config_when_nothing_is_live_yet(self):
        """A first install legitimately has no clients. Refusing here would
        mean the spool never gets created on a new deployment."""
        result = write_clients_conf_spool(reason="first run")

        self.assertEqual(result, self.spool)
        self.assertEqual(count_client_blocks(open(self.spool).read()), 0)

    @override_settings(RADIUS_ALLOW_EMPTY_CLIENTS=True)
    def test_the_override_lets_the_list_really_be_emptied(self):
        """Decommissioning is real. The guard has to be deliberate to get
        past, not impossible -- a guard that blocks legitimate work gets
        switched off permanently within a week."""
        make_client()
        write_clients_conf_spool(reason="initial")

        RadiusNasClient.objects.update(is_active=False)
        result = write_clients_conf_spool(reason="decommissioned")

        self.assertEqual(result, self.spool)
        self.assertEqual(count_client_blocks(open(self.spool).read()), 0)

    def test_removing_one_client_of_several_still_writes(self):
        """Only zero-over-something is refused. Deleting one NAS client is
        ordinary admin work and must not need an override."""
        keep = make_client()
        drop = make_client(name="KLB PE/GNE", ip_address="10.200.0.8", shortname="KLB-PE", realm="")
        write_clients_conf_spool(reason="both")

        drop.delete()
        write_clients_conf_spool(reason="one removed")

        written = open(self.spool).read()
        self.assertEqual(count_client_blocks(written), 1)
        self.assertIn(keep.shortname, written)


class StateTests(SpoolDirTestCase):
    """clients_conf_state is what lets a screen say the network is dark.
    Its whole purpose is to disagree with the client list when the list and
    the running config have come apart."""

    def test_it_reports_an_outage_the_host_recorded(self):
        make_client()
        write_clients_conf_spool(reason="initial")
        self.write_status(
            state="outage", detail="rolled back to a config with 0 clients",
            spool_clients=1, live_clients=0, at="2026-09-20T18:21:20Z",
        )

        state = clients_conf_state()

        self.assertEqual(state["active_clients"], 1)
        self.assertEqual(state["spool_clients"], 1)
        self.assertTrue(any("no NAS clients" in p for p in state["problems"]))

    def test_it_reports_a_config_that_was_never_applied(self):
        """Sunday's other half: the fix existed and had simply not reached
        the server. Nothing on any screen said so."""
        make_client()
        make_client(name="KLB PE/GNE", ip_address="10.200.0.8", shortname="KLB-PE", realm="")
        write_clients_conf_spool(reason="two clients")
        self.write_status(
            state="ok", detail="installed 1 client(s)",
            spool_clients=1, live_clients=1, at="2026-09-20T18:00:00Z",
        )

        problems = clients_conf_state()["problems"]

        self.assertTrue(any("has not been applied" in p for p in problems))

    def test_a_healthy_server_reports_no_problems(self):
        """A check that cries wolf is worse than none: staff learn to skip
        the banner, and the one time it matters they skip that too."""
        make_client()
        write_clients_conf_spool(reason="initial")
        self.write_status(
            state="ok", detail="installed 1 client(s)",
            spool_clients=1, live_clients=1, at="2026-09-21T04:45:32Z",
        )

        self.assertEqual(clients_conf_state()["problems"], [])

    def test_it_says_so_when_the_host_has_never_reported_back(self):
        """No status file means the watcher is not running, or has never
        run. That is indistinguishable from a healthy server from in here,
        so it must not be reported as healthy."""
        make_client()
        write_clients_conf_spool(reason="initial")

        problems = clients_conf_state()["problems"]

        self.assertTrue(any("not reported back" in p for p in problems))


class AuditTests(TestCase):
    """Who changed the NAS clients, and when. On 2026-09-20 twelve writes
    landed on these rows and the platform could not name one of them."""

    def setUp(self):
        self.client = APIClient()
        self.actor = User.objects.create_user(
            username="fanie", password="x", role="technician", is_staff=True,
            allowed_sections=ALL_SECTIONS,
        )
        self.client.force_authenticate(self.actor)

    def _post(self, **overrides):
        body = {
            "name": "Skybre-PPP-Teraco-JHB",
            "ip_address": "102.23.159.30",
            "shortname": "teraco-jhb",
            "secret": "a-shared-secret",
            "realm": "jhb",
        }
        body.update(overrides)
        return self.client.post("/api/radius-nas-clients/", body, format="json")

    def test_creating_records_who_did_it(self):
        response = self._post()

        self.assertEqual(response.status_code, 201, response.data)
        entry = RadiusNasClientAudit.objects.get()
        self.assertEqual(entry.action, "created")
        self.assertEqual(entry.changed_by, self.actor)
        self.assertEqual(entry.shortname, "teraco-jhb")

    def test_deactivating_records_the_field_that_moved(self):
        """The single most important row this table will ever hold: the
        change that takes a NAS client out of the generated config."""
        client = make_client()

        response = self.client.patch(
            f"/api/radius-nas-clients/{client.pk}/", {"is_active": False}, format="json"
        )

        self.assertEqual(response.status_code, 200, response.data)
        entry = RadiusNasClientAudit.objects.filter(action="updated").get()
        self.assertEqual(entry.changed_fields, ["is_active"])
        self.assertEqual(entry.changed_by, self.actor)

    def test_the_secret_is_named_but_never_stored(self):
        """An audit table is exported and shared more casually than the
        screen it audits. Knowing the secret changed is the useful part;
        keeping a copy of it is a worse leak than the gap it fills."""
        client = make_client()

        self.client.patch(
            f"/api/radius-nas-clients/{client.pk}/",
            {"secret": "a-brand-new-secret"}, format="json",
        )

        entry = RadiusNasClientAudit.objects.filter(action="updated").get()
        self.assertEqual(entry.changed_fields, ["secret"])
        stored = json.dumps(list(RadiusNasClientAudit.objects.values()), default=str)
        self.assertNotIn("a-brand-new-secret", stored)
        self.assertNotIn("a-shared-secret", stored)

    def test_deleting_names_the_person_who_deleted_it(self):
        """Not the last person to save it. The row's own changed_by holds
        the previous saver, and reporting that on a delete would put an
        innocent name against the most consequential action available."""
        client = make_client()
        client.changed_by = User.objects.create_user(
            username="someone-else", password="x", role="technician", is_staff=True,
            allowed_sections=ALL_SECTIONS,
        )
        client.save()

        response = self.client.delete(f"/api/radius-nas-clients/{client.pk}/")

        self.assertEqual(response.status_code, 204)
        entry = RadiusNasClientAudit.objects.filter(action="deleted").get()
        self.assertEqual(entry.changed_by, self.actor)
        self.assertEqual(entry.shortname, "teraco-jhb")
        self.assertEqual(entry.client_pk, client.pk)

    def test_an_audit_row_survives_the_client_being_deleted(self):
        """Otherwise the record disappears exactly when it is needed."""
        client = make_client()
        client_pk = client.pk
        self.client.delete(f"/api/radius-nas-clients/{client_pk}/")

        self.assertFalse(RadiusNasClient.objects.filter(pk=client_pk).exists())
        self.assertTrue(RadiusNasClientAudit.objects.filter(client_pk=client_pk).exists())

    def test_a_save_that_changes_nothing_writes_no_row(self):
        """A table read during an outage has to be worth reading. Rows for
        saves that moved nothing bury the one that did."""
        client = make_client()
        RadiusNasClientAudit.objects.all().delete()

        client.save()

        self.assertEqual(RadiusNasClientAudit.objects.count(), 0)

    def test_a_change_made_outside_the_api_is_still_recorded(self):
        """Management commands and shell sessions are exactly the changes
        nobody remembers making. Recorded with no user rather than not
        recorded at all."""
        client = make_client()
        RadiusNasClientAudit.objects.all().delete()

        client.is_active = False
        client.save()

        entry = RadiusNasClientAudit.objects.get()
        self.assertEqual(entry.changed_fields, ["is_active"])
        self.assertIsNone(entry.changed_by)

    def test_the_audit_endpoint_is_gated_on_networking_access(self):
        make_client()
        self.client.force_authenticate(
            User.objects.create_user(
                username="bookkeeper", password="x", role="accounts", is_staff=True,
                allowed_sections=["finance"],
            )
        )

        self.assertEqual(
            self.client.get("/api/radius-nas-clients/audit/").status_code, 403
        )

    def test_the_audit_endpoint_returns_newest_first(self):
        client = make_client()
        self.client.patch(
            f"/api/radius-nas-clients/{client.pk}/", {"description": "moved"}, format="json"
        )

        response = self.client.get("/api/radius-nas-clients/audit/")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data[0]["action"], "updated")
        self.assertEqual(response.data[0]["changed_by_username"], "fanie")
        self.assertNotIn("secret", json.dumps(response.data))


class TestDatabaseGuardTests(TestCase):
    """A test run must never be able to write the live RADIUS config.

    This is the actual cause of the 2026-09-20 outage, and it was not a
    subtle one once seen. Signals fire in tests exactly as they do in
    production. test_enforcement.py deletes every RadiusNasClient in the
    course of its work; post_delete fired, the renderer ran against the
    TEST database, found no clients because the test had just removed
    them, and wrote that empty config to the host's real spool file. The
    watcher installed it on the live server, FreeRADIUS restarted with no
    clients, and every router on the network was ignored for ten hours.

    Note what these tests do NOT rely on: how the test suite was started.
    The check is made on the database connection, because `manage.py
    test`, pytest, a CI runner and a developer's shell all reach this
    code by the same route and all four have to be stopped.
    """

    def setUp(self):
        self.spool_dir = tempfile.mkdtemp(prefix="skybre-spool-guard-")
        self.spool = os.path.join(self.spool_dir, "clients.conf")

    def test_it_refuses_to_write_while_running_on_a_test_database(self):
        make_client()

        with override_settings(RADIUS_CLIENTS_SPOOL=self.spool):
            result = write_clients_conf_spool(reason="a test that forgot to opt in")

        self.assertIsNone(result)
        self.assertFalse(os.path.exists(self.spool))

    def test_deleting_every_client_writes_nothing_at_all(self):
        """The exact teardown that caused the outage, run against the
        guard. Before this, it produced an empty clients.conf on the live
        RADIUS server."""
        make_client()

        with override_settings(RADIUS_CLIENTS_SPOOL=self.spool):
            RadiusNasClient.objects.all().delete()

        self.assertFalse(os.path.exists(self.spool))

    def test_the_connected_database_really_is_a_test_one(self):
        """If this ever fails, the guard above is passing for the wrong
        reason and is protecting nothing."""
        from django.db import connection

        self.assertTrue(connection.settings_dict["NAME"].startswith("test_"))
