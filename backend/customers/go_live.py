"""Active -> Online, automatically, when a client's line first connects.

Rudolph, 6 Oct 2026: "Once a customer goes live status should automatically
move from active to Online" -- and "goes live" means one of their lines has
logged on to the network (an open RADIUS session that is still reporting,
the same test the Connection column uses). Run every minute from the
sample_session_usage job, which already reads radacct.

Only ACTIVE clients move. New, Suspended, Bad Debt and Inactive are left
alone: those are decisions somebody made, and a line connecting doesn't
overrule them. Online is never moved back automatically.

save(), not update(), so the client's History shows the change.
"""
import datetime

from django.db.models import Q
from django.utils import timezone

from .models import Customer

#: Same staleness rule as radiusauth.usage.LIVE_SESSION_STALE_SECONDS: an
#: "open" session that hasn't reported for this long is a lost stop packet,
#: not a live line.
STALE_SECONDS = 900


def promote_live_customers(now=None):
    from billing.models import Service
    from radiusauth.models import RadAcct

    now = now or timezone.now()
    cutoff = now - datetime.timedelta(seconds=STALE_SECONDS)
    live_usernames = (
        RadAcct.objects.filter(acctstoptime__isnull=True)
        .filter(Q(acctupdatetime__gte=cutoff) | Q(acctupdatetime__isnull=True, acctstarttime__gte=cutoff))
        .exclude(username="")
        .values("username")
    )
    customer_ids = (
        Service.objects.filter(radius_username__in=live_usernames)
        .exclude(status=Service.Status.TERMINATED)
        .values("customer_id")
    )
    promoted = 0
    for customer in Customer.objects.filter(status=Customer.Status.ACTIVE, pk__in=customer_ids):
        customer.status = Customer.Status.ONLINE
        customer.save(update_fields=["status", "updated_at"])
        promoted += 1
    return promoted
