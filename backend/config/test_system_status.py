"""Dashboard System status card (Rudolph, 7 Oct 2026)."""
import os
import tempfile
import time

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User
from config import system_status


class SystemStatusTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="boss", password="x", role="admin")
        self.api = APIClient()
        self.api.force_authenticate(self.admin)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_admin_only(self):
        support = User.objects.create_user(username="s", password="x", role="support", allowed_sections=["configs"])
        api = APIClient()
        api.force_authenticate(support)
        self.assertEqual(api.get("/api/system-status/").status_code, 403)
        self.assertEqual(APIClient().get("/api/system-status/").status_code, 401)

    def test_reads_the_server(self):
        with override_settings(SYSTEM_BACKUP_DIR=self.tmp.name,
                               SYSTEM_REMOTE_BACKUP_STAMP=os.path.join(self.tmp.name, ".remote-backup-ok")):
            res = self.api.get("/api/system-status/")
        self.assertEqual(res.status_code, 200, res.data)
        d = res.data
        self.assertGreater(d["cpu_cores"], 0)
        self.assertEqual(len(d["load"]), 3)
        self.assertTrue(0 <= d["cpu_pct"] <= 100)
        self.assertGreater(d["mem_total"], 0)
        self.assertGreater(d["disk_total"], 0)
        self.assertTrue(d["db_backup_configured"])
        self.assertIsNone(d["db_backup"])
        self.assertFalse(d["remote_backup_configured"])

    def test_backups(self):
        old = os.path.join(self.tmp.name, "isp-2026-10-05.sql.gz")
        new = os.path.join(self.tmp.name, "daily", "isp-2026-10-07.sql.gz")
        os.makedirs(os.path.dirname(new))
        for path, size in ((old, 10), (new, 2048)):
            with open(path, "wb") as fh:
                fh.write(b"x" * size)
        now = time.time()
        os.utime(old, (now - 50 * 3600, now - 50 * 3600))
        os.utime(new, (now - 3 * 3600, now - 3 * 3600))
        stamp = os.path.join(self.tmp.name, ".remote-backup-ok")
        open(stamp, "w").close()
        os.utime(stamp, (now - 30 * 3600, now - 30 * 3600))
        with override_settings(SYSTEM_BACKUP_DIR=self.tmp.name, SYSTEM_REMOTE_BACKUP_STAMP=stamp):
            b = system_status.backups(now)
        self.assertEqual((b["db_backup"]["file"], b["db_backup"]["size"], b["db_backup"]["ok"]),
                         ("isp-2026-10-07.sql.gz", 2048, True))
        self.assertEqual(b["db_backup"]["hours"], 3.0)
        self.assertFalse(b["remote_backup"]["ok"], "30 h old is stale")

    def test_not_mounted(self):
        with override_settings(SYSTEM_BACKUP_DIR="/nonexistent-backups", SYSTEM_REMOTE_BACKUP_STAMP=""):
            b = system_status.backups()
        self.assertEqual((b["db_backup_configured"], b["db_backup"], b["remote_backup"]), (False, None, None))
