"""Source blocks inserted by apply_radius_testdb_guard.py."""

TEST_DB_HELPER = '''def _running_against_test_database():
    """True when the connected database is a Django test database.

    Checked on the CONNECTION, not on sys.argv and not on a settings flag,
    because the failure this prevents does not care how the tests were
    started. `manage.py test`, pytest, a CI runner and a developer's shell
    all arrive here by the same route: a signal fires, the renderer runs
    against whatever rows the test just made, and the result is written
    over the live RADIUS server's configuration.

    That is not a hypothetical. On 2026-09-20 it happened. A test suite
    run deleted every RadiusNasClient as part of its ordinary work;
    post_delete fired, the render came back empty because the TEST
    database had no clients left in it, and the host installed that empty
    config on the production RADIUS server. FreeRADIUS restarted cleanly
    with no clients at all and ignored every router on the network for
    ten hours -- routers retransmitting into silence, accounting stopped,
    every customer reading as offline, and the daemon reporting itself
    healthy throughout.

    The other renders that evening failed validation for a related
    reason: the test fixture's NAS sits on 127.0.0.1, which collides with
    FreeRADIUS's stock `client localhost`, and a duplicate client is a
    fatal config error. So of everything the tests produced, the only
    thing that could install itself was the empty file.
    """
    from django.db import connection

    return str(connection.settings_dict.get("NAME") or "").startswith("test_")


'''

TEST_DB_GUARD = '''
    # A test run must never write the live RADIUS configuration. See
    # _running_against_test_database() for the outage that makes this
    # non-negotiable.
    #
    # Tests that genuinely mean to exercise the spool opt in with
    # RADIUS_CLIENTS_SPOOL_TEST_OK and point RADIUS_CLIENTS_SPOOL at a
    # directory of their own. Everything else gets silence -- debug
    # rather than error, because during a test run this is the expected
    # outcome many times over and a warning per occurrence would train
    # people to ignore the log.
    if _running_against_test_database() and not getattr(
        settings, "RADIUS_CLIENTS_SPOOL_TEST_OK", False
    ):
        logger.debug(
            "Not writing the clients.conf spool: running against a test "
            "database. (%s)", reason or "no reason given",
        )
        return None
'''
