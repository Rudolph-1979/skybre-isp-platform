"""Is this session authenticated by THIS platform?

`radacct` is written by FreeRADIUS and contains whatever the NASes sent.
That is not the same thing as a list of the services this platform runs.
On 2026-09-15, 48 of 55 open sessions on Rooiskool and FWH belonged to
Grunder Co customers who live on SPLYNX, not here -- same company, same
routers, different platform. Their CPE attaches to the PPPoE server whose
service-name is "Skybre", which makes their Called-Station-Id match our
`/radius` entry, which sits first in the router's list -- so RouterOS
sends us their accounting even though we reject every one of their
logins.

They are our customers. They are not our SESSIONS: Splynx authenticates
them, Splynx bills them, and Splynx is where their usage has to be
counted. Everything here is about not confusing this platform's records
with that platform's customers -- never about excluding anyone.

This module exists so that question has exactly ONE answer. The Live
Sessions page and the prune command were written with two slightly
different rules, and the first test run caught them disagreeing about a
service whose RADIUS login had been switched off. Two definitions of
"ours" in one platform, disagreeing on screen, is the same failure mode
radiusauth/offline.py's docstring warns about for "online".

The rule, in two parts, because either alone is wrong:

* `Service.radius_username` -- the platform's own record of who we
  authenticate. The primary source.
* `radcheck.username` -- what FreeRADIUS is actually willing to let in.
  Included because radcheck rows are torn down whenever RADIUS login is
  disabled on a service (see signals._clear_radius_entries), and because
  a name FreeRADIUS accepts is by definition one we put there, whatever
  the services table currently says.

A session with no username at all is neither. It cannot be matched to a
service, and it is not evidence of a Splynx line either, so it is
reported as not-ours for display but is never a candidate for deletion.
"""
from django.db.models import Q


def ours_q(field="username"):
    """Q matching rows whose `field` is a RADIUS username we own."""
    from billing.models import Service

    from .models import RadCheck

    services = (
        Service.objects.exclude(radius_username__isnull=True)
        .exclude(radius_username="")
        .values("radius_username")
    )
    checks = RadCheck.objects.values("username")
    return Q(**{f"{field}__in": services}) | Q(**{f"{field}__in": checks})


def not_ours_q(field="username"):
    """Q matching rows that are NOT ours, including nameless ones.

    The isnull branch is not decoration. `NOT (NULL IN (...))` evaluates
    to NULL in SQL, not to true, so a row with no username would fall out
    of both sides and appear in neither list -- present in the table,
    invisible everywhere.
    """
    return Q(**{f"{field}__isnull": True}) | ~ours_q(field)


def known_username_count():
    """How many RADIUS usernames this platform knows about.

    Callers that delete use this as a sanity check: zero means the
    services table didn't load, not that we have no customers, and the
    difference between those two readings is the entire accounting table.
    """
    from billing.models import Service

    from .models import RadCheck

    services = (
        Service.objects.exclude(radius_username__isnull=True)
        .exclude(radius_username="")
        .values_list("radius_username", flat=True)
    )
    checks = RadCheck.objects.values_list("username", flat=True)
    return len(set(services) | set(checks))
