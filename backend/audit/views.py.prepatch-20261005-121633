import datetime

from django.db.models import Q
from rest_framework import permissions, viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsStaffMember, section_permission
from customers.models import Customer

from .auth_events import record_logout
from .models import AuditEvent
from .serializers import AuditEventSerializer, CustomerSessionSerializer

HasConfigsAccess = section_permission("configs")
HasCustomersAccess = section_permission("customers")


class AuditEventViewSet(viewsets.ReadOnlyModelViewSet):
    """The platform-wide activity log.

    Read-only at every level, including for Admin. There is no edit or
    delete endpoint anywhere in this app, and that is the point: a trail
    the people it records can rewrite is not a trail. Pruning old rows is
    a deliberate act performed with `prune_audit_log` on the server, not
    something reachable from a browser session.

    Gated on `configs` rather than on the section each event came from.
    An activity log necessarily spans everything -- who edited an
    invoice, who changed permissions -- so scoping it per-section would
    either leak finance events to support staff or produce a log with
    holes in it that reads as if nothing happened.
    """

    serializer_class = AuditEventSerializer
    permission_classes = [permissions.IsAuthenticated, IsStaffMember, HasConfigsAccess]
    filterset_fields = ["action", "actor", "target_type", "customer"]
    search_fields = ["actor_label", "target_label", "detail"]
    ordering_fields = ["created_at"]

    def get_queryset(self):
        qs = AuditEvent.objects.select_related("actor", "customer").all()
        params = self.request.query_params

        since = params.get("since")
        if since:
            parsed = _parse_date(since)
            if parsed:
                qs = qs.filter(created_at__gte=parsed)
        until = params.get("until")
        if until:
            parsed = _parse_date(until)
            if parsed:
                # Inclusive of the whole day the user typed.
                qs = qs.filter(created_at__lt=parsed + datetime.timedelta(days=1))

        kind = params.get("kind")
        if kind == "auth":
            qs = qs.filter(
                action__in=[
                    AuditEvent.Action.LOGIN,
                    AuditEvent.Action.LOGIN_FAILED,
                    AuditEvent.Action.LOGOUT,
                ]
            )
        elif kind == "changes":
            qs = qs.filter(
                action__in=[
                    AuditEvent.Action.CREATED,
                    AuditEvent.Action.UPDATED,
                    AuditEvent.Action.DELETED,
                ]
            )
        return qs


def _parse_date(value):
    try:
        return datetime.datetime.combine(
            datetime.date.fromisoformat(value), datetime.time.min, tzinfo=datetime.timezone.utc
        )
    except ValueError:
        return None


class CustomerHistoryView(APIView):
    """Everything that happened to one customer, for the History tab.

    Gated on `customers`, not `configs`: this is the support-desk view of
    a single customer's record, and the people answering "why was my line
    cut off" are the ones who need it. It is scoped to one customer, so
    it does not carry the platform-wide exposure that makes the activity
    log an admin-level screen.
    """

    permission_classes = [permissions.IsAuthenticated, IsStaffMember, HasCustomersAccess]

    def get(self, request, pk):
        customer = _visible_customer(request.user, pk)
        if customer is None:
            # 404 rather than 403 -- a 403 would confirm the id exists to
            # somebody whose partner scoping is meant to hide it.
            return Response({"detail": "Not found."}, status=404)

        events = (
            AuditEvent.objects.select_related("actor")
            .filter(
                Q(customer=customer)
                | Q(target_type="customers.Customer", target_id=str(customer.pk))
            )
            .order_by("-created_at")[:500]
        )
        return Response({"results": AuditEventSerializer(events, many=True).data})


class CustomerSessionsView(APIView):
    """One customer's RADIUS session history.

    The data has existed since the platform's first release -- FreeRADIUS
    writes radacct on every connect and disconnect -- but it was only
    readable through Networking, which most support staff cannot open.
    Nothing new is collected here; it is the same table, asked a
    customer-shaped question and gated to match who needs the answer.
    """

    permission_classes = [permissions.IsAuthenticated, IsStaffMember, HasCustomersAccess]

    def get(self, request, pk):
        from radiusauth.models import RadAcct

        customer = _visible_customer(request.user, pk)
        if customer is None:
            return Response({"detail": "Not found."}, status=404)

        usernames = [
            u
            for u in customer.services.values_list("radius_username", flat=True)
            if u
        ]
        if not usernames:
            return Response({"results": [], "usernames": []})

        try:
            days = max(1, min(int(request.query_params.get("days", 30)), 365))
        except (TypeError, ValueError):
            days = 30
        from django.utils import timezone

        cutoff = timezone.now() - datetime.timedelta(days=days)

        rows = RadAcct.objects.filter(username__in=usernames).filter(
            Q(acctstarttime__gte=cutoff) | Q(acctstoptime__isnull=True)
        ).order_by("-acctstarttime")[:500]

        return Response(
            {
                "results": CustomerSessionSerializer(
                    [_session_row(r) for r in rows], many=True
                ).data,
                "usernames": usernames,
            }
        )


def _session_row(row):
    from django.utils import timezone

    # Duration is COMPUTED from the start time rather than read from
    # acctsessiontime, which the NAS only writes on an interim update or
    # a stop. A session that connected four minutes ago has
    # acctsessiontime = NULL, and reporting that as "0m" makes a live
    # connection look like a failed one.
    end = row.acctstoptime or timezone.now()
    duration = None
    if row.acctstarttime:
        duration = max(0, int((end - row.acctstarttime).total_seconds()))
    return {
        "session_id": row.acctsessionid or str(row.radacctid),
        "started_at": row.acctstarttime,
        "ended_at": row.acctstoptime,
        "duration_seconds": duration,
        "ip_address": row.framedipaddress,
        # RADIUS counts from the NAS's point of view: what arrives AT the
        # NAS is the customer's upload. Reversing these is invisible
        # until somebody notices their download tracks their upload.
        "download_bytes": row.acctoutputoctets or 0,
        "upload_bytes": row.acctinputoctets or 0,
        "terminate_cause": row.acctterminatecause or "",
        "username": row.username or "",
        "active": row.acctstoptime is None,
    }


def _visible_customer(user, pk):
    """Respects the same partner scoping as the customer list.

    Without this, a staff member restricted to one reseller's customers
    could read any other customer's history by id -- the History tab
    would become the hole in a restriction applied everywhere else.
    """
    qs = Customer.objects.all()
    allowed = getattr(user, "allowed_partners", None) or []
    if allowed and user.role != user.Role.ADMIN:
        qs = qs.filter(Q(partner_id__in=allowed) | Q(partner__isnull=True))
    return qs.filter(pk=pk).first()


@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated])
def sign_out(request):
    """Records a deliberate sign-out.

    Worth saying plainly: this only fires when somebody clicks Sign out.
    Closing the tab, or a token simply expiring, records nothing -- there
    is no server-side session to end, so there is no event to observe.
    The absence of a sign-out therefore means nothing at all, and should
    never be read as somebody staying signed in.
    """
    record_logout(request.user)
    return Response({"ok": True})
