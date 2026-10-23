"""Client CPE signal for the client's Services table (8 Oct 2026).

GET  /api/cpe-signals/?customer=<id>            current reading per line
GET  /api/cpe-signals/<service>/history/?hours=  samples for the graph
POST /api/cpe-signals/<service>/refresh/        read the AP now
"""
from django.utils import timezone
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsStaffMember, section_permission
from customers.scoping import scope_by_customer

from .models import CpeSignal, CpeSignalSample

HasServicesAccess = section_permission("services")
PERMS = [permissions.IsAuthenticated, IsStaffMember, HasServicesAccess]


def _services(user):
    from billing.models import Service

    return scope_by_customer(Service.objects.all(), user, allow_null=False)


def serialize(sig):
    return {
        "service": sig.service_id,
        "state": sig.state,
        "mac": sig.mac,
        "ap": sig.ap_id,
        "ap_name": sig.ap.name if sig.ap_id else None,
        "interface": sig.interface,
        "radio_name": sig.radio_name,
        "signal_dbm": sig.signal_dbm,
        "tx_signal_dbm": sig.tx_signal_dbm,
        "snr_db": sig.snr_db,
        "tx_ccq": sig.tx_ccq,
        "rx_ccq": sig.rx_ccq,
        "tx_rate": sig.tx_rate,
        "rx_rate": sig.rx_rate,
        "uptime": sig.uptime,
        "distance_km": sig.distance_km,
        "seen_at": sig.seen_at,
        "checked_at": sig.checked_at,
    }


class CpeSignalListView(APIView):
    permission_classes = PERMS

    def get(self, request):
        customer = request.query_params.get("customer")
        if not customer:
            return Response({"detail": "customer is required."}, status=400)
        ids = _services(request.user).filter(customer_id=customer).values_list("pk", flat=True)
        rows = CpeSignal.objects.filter(service_id__in=list(ids)).select_related("ap")
        return Response({"results": [serialize(r) for r in rows]})


class CpeSignalHistoryView(APIView):
    permission_classes = PERMS

    def get(self, request, service):
        if not _services(request.user).filter(pk=service).exists():
            return Response({"detail": "Not found."}, status=404)
        try:
            hours = max(1, min(int(request.query_params.get("hours", "24")), 24 * 7))
        except ValueError:
            hours = 24
        since = timezone.now() - timezone.timedelta(hours=hours)
        rows = CpeSignalSample.objects.filter(service_id=service, at__gte=since).values(
            "at", "signal_dbm", "tx_signal_dbm", "tx_ccq", "rx_ccq"
        )
        return Response({"hours": hours, "results": list(rows)})


class CpeSignalRefreshView(APIView):
    permission_classes = PERMS

    def post(self, request, service):
        from .cpe_signals import refresh_service

        svc = _services(request.user).filter(pk=service).first()
        if svc is None:
            return Response({"detail": "Not found."}, status=404)
        sig = refresh_service(svc)
        return Response(serialize(CpeSignal.objects.select_related("ap").get(pk=sig.pk)))
