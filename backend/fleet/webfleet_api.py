"""Webfleet vehicles on Networking -> Map (#186) and today's speeding for
the Dashboard tile (#188). Everything else Webfleet gives goes into the
existing vehicle data (see fleet/webfleet.py)."""
from rest_framework import permissions
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsStaffMember

from . import webfleet


class VehiclesOrScheduling(BasePermission):
    """Vehicle positions: Vehicles staff, and Scheduling (who see the
    technicians on the same map)."""
    message = "You need the Vehicles or Scheduling section."

    def has_permission(self, request, view):
        from accounts.permissions import user_can_access_section

        return any(user_can_access_section(request.user, s) for s in ("vehicles", "scheduling"))


def _iso(dt):
    return dt.isoformat() if dt else None


class WebfleetPositionsView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsStaffMember, VehiclesOrScheduling]

    def get(self, request):
        if not webfleet.configured():
            return Response({"configured": False, "results": []})
        try:
            rows = webfleet.objects()
        except webfleet.WebfleetError as exc:
            return Response({"configured": True, "detail": str(exc), "results": []})
        linked = webfleet.linked_vehicles()
        out = []
        for r in rows:
            v = linked.get(r["objectno"])
            out.append({
                **{k: r[k] for k in ("objectno", "name", "lat", "lng", "place", "speed_kmh", "course", "ignition", "standstill", "driver_name", "odometer_km")},
                "pos_time": _iso(r["pos_time"]),
                "vehicle_id": v.pk if v else None,
                "registration": v.registration_number if v else "",
                "vehicle_name": f"{v.make} {v.model}".strip() if v else "",
            })
        return Response({"configured": True, "results": out})


class WebfleetSpeedingView(APIView):
    """Today's speeding (#188): for the Dashboard tile. Read from what the
    15-minute sync stored -- no Webfleet call on page load."""
    permission_classes = [permissions.IsAuthenticated, IsStaffMember, VehiclesOrScheduling]

    def get(self, request):
        from django.utils import timezone

        from .models import WebfleetSpeeding

        if not webfleet.configured():
            return Response({"configured": False, "count": 0, "results": []})
        today = timezone.localdate()
        rows = WebfleetSpeeding.objects.filter(started_at__date=today).select_related("vehicle")
        return Response({
            "configured": True,
            "count": rows.count(),
            "results": [{
                "id": e.pk, "vehicle_id": e.vehicle_id, "registration": e.vehicle.registration_number,
                "vehicle_name": f"{e.vehicle.make} {e.vehicle.model}".strip(),
                "started_at": _iso(e.started_at), "max_speed_kmh": e.max_speed_kmh, "avg_speed_kmh": e.avg_speed_kmh,
                "limit_kmh": e.limit_kmh, "place": e.place, "driver_name": e.driver_name, "duration_s": e.duration_s,
            } for e in rows],
        })
