"""Background router pushes must survive the process that started them.

`_after_commit_in_background` runs the router half of every Service
change -- dropping the live session, updating the block list -- in a
daemon thread started on commit. That is right under gunicorn, where the
worker lives for months. Under `manage.py` it was catastrophic and
silent: handle() returns, the interpreter exits, and Python kills daemon
threads without joining them, so a push needing eight seconds to reach a
router got microseconds.

Which meant every unattended job's router half simply did not happen.
apply_cancellations terminated the service, printed "Ended 3 service(s)",
and never dropped the session -- and since FreeRADIUS is only consulted
at login, those customers kept full internet on the session they already
had, for weeks on a stable link. apply_tariff_changes printed "Live
sessions were dropped so the new speed applies now", which the cron path
could not make true. Nothing recorded any of it, because RadiusAction
rows are written by enforcement.apply_change, which never ran.

It survived because the two commands a human runs and watches --
resync_radius --kick and apply_speed_policies -- call apply_change inline.
"""
import atexit
import threading
import time
from unittest import mock

from django.test import TestCase, TransactionTestCase

from network import signals as network_signals


class DrainRegistrationTests(TestCase):
    def test_the_drain_is_registered_with_atexit_at_import(self):
        """If this registration is ever dropped, every cron job silently
        goes back to discarding its router work -- so it is worth a test
        of its own rather than trusting the import.

        atexit exposes no list of callbacks, so the module is reloaded
        with atexit.register patched and the call is observed.
        """
        import importlib

        with mock.patch.object(atexit, "register") as register:
            importlib.reload(network_signals)
        registered = [call.args[0] for call in register.call_args_list if call.args]
        self.assertTrue(
            any(getattr(fn, "__name__", "") == "_drain_pending_work" for fn in registered),
            f"_drain_pending_work was not registered with atexit: {registered}",
        )

    def test_draining_with_nothing_pending_returns_immediately(self):
        network_signals._pending.clear()
        started = time.monotonic()
        network_signals._drain_pending_work(timeout=5)
        self.assertLess(time.monotonic() - started, 0.5)


class DrainWaitsTests(TransactionTestCase):
    """TransactionTestCase because on_commit callbacks only fire on a real
    commit, which is what schedules the thread in the first place."""

    def setUp(self):
        network_signals._pending.clear()

    def tearDown(self):
        network_signals._drain_pending_work(timeout=5)
        network_signals._pending.clear()

    def test_a_push_is_registered_before_it_starts(self):
        """Registered before start(), so a process exiting immediately
        after the save still sees it -- otherwise the drain can look at an
        empty set while a thread is being created."""
        seen = threading.Event()
        release = threading.Event()

        def _work():
            seen.set()
            release.wait(timeout=5)

        from django.db import transaction

        with transaction.atomic():
            network_signals._after_commit_in_background("test push", _work)
            # Still inside the transaction: on_commit has not fired.
            self.assertEqual(len(network_signals._pending), 0)

        self.assertTrue(seen.wait(timeout=5))
        self.assertEqual(len(network_signals._pending), 1)
        release.set()

    def test_the_drain_waits_for_a_push_to_finish(self):
        """The actual fix. Without the drain this work never completes."""
        finished = []

        def _work():
            time.sleep(0.3)
            finished.append(True)

        from django.db import transaction

        with transaction.atomic():
            network_signals._after_commit_in_background("slow push", _work)

        # Exactly what atexit does at interpreter shutdown.
        network_signals._drain_pending_work(timeout=5)
        self.assertEqual(finished, [True], "the push was abandoned rather than waited for")
        self.assertEqual(len(network_signals._pending), 0)

    def test_a_finished_push_deregisters_itself(self):
        from django.db import transaction

        with transaction.atomic():
            network_signals._after_commit_in_background("quick push", lambda: None)
        network_signals._drain_pending_work(timeout=5)
        self.assertEqual(len(network_signals._pending), 0)

    def test_a_failing_push_deregisters_itself_too(self):
        """Otherwise one exception leaves a dead thread in the set and
        every later drain waits on it."""
        from django.db import transaction

        def _boom():
            raise RuntimeError("router said no")

        with self.assertLogs("network.signals", level="ERROR"):
            with transaction.atomic():
                network_signals._after_commit_in_background("failing push", _boom)
            network_signals._drain_pending_work(timeout=5)
        self.assertEqual(len(network_signals._pending), 0)

    def test_the_drain_gives_up_rather_than_hanging_forever(self):
        """A hung router must not stop the process exiting. Bounded wait,
        and it says so in the log."""
        release = threading.Event()

        def _hang():
            release.wait(timeout=30)

        from django.db import transaction

        with transaction.atomic():
            network_signals._after_commit_in_background("hung push", _hang)

        started = time.monotonic()
        with self.assertLogs("network.signals", level="ERROR") as logged:
            network_signals._drain_pending_work(timeout=1)
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 5, "the drain did not respect its timeout")
        self.assertTrue(
            any("did not reach the router" in line for line in logged.output),
            f"the give-up was not reported: {logged.output}",
        )
        release.set()

    def test_several_pushes_are_all_waited_for(self):
        done = []

        def _work(n):
            time.sleep(0.1)
            done.append(n)

        from django.db import transaction

        with transaction.atomic():
            for n in range(4):
                network_signals._after_commit_in_background(
                    f"push {n}", lambda n=n: _work(n)
                )

        network_signals._drain_pending_work(timeout=10)
        self.assertCountEqual(done, [0, 1, 2, 3])
