"""The Support page's API (#212). See tickets.support for what every figure
means."""
import datetime

from rest_framework import permissions, serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsStaffMember, section_permission

from .models import SupportSettings
from .support import PERIODS, dashboard

HasTicketsAccess = section_permission("tickets")


class SupportDashboardView(APIView):
    """GET /api/support/dashboard/?period=today|week|month -- the whole page."""

    permission_classes = [permissions.IsAuthenticated, IsStaffMember, HasTicketsAccess]

    def get(self, request):
        period = request.query_params.get("period", "week")
        if period not in PERIODS:
            return Response({"detail": "period must be today, week or month."}, status=400)
        return Response(dashboard(request.user, period))


class SupportSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = SupportSettings
        fields = [
            "reply_target_minutes", "office_start", "office_end", "office_days",
            "outage_min_clients", "outage_window_minutes", "updated_at",
        ]
        read_only_fields = ["updated_at"]

    def validate_reply_target_minutes(self, value):
        if not 1 <= value <= 10080:
            raise serializers.ValidationError("Between 1 minute and a week.")
        return value

    def validate_office_days(self, value):
        if not isinstance(value, list) or any(not isinstance(d, int) or not 0 <= d <= 6 for d in value):
            raise serializers.ValidationError("A list of weekdays, 0 = Monday ... 6 = Sunday.")
        if not value:
            raise serializers.ValidationError("Pick at least one office day.")
        return sorted(set(value))

    def validate_outage_min_clients(self, value):
        if value < 2:
            raise serializers.ValidationError("At least 2 -- one client going offline is not an outage.")
        return value

    def validate_outage_window_minutes(self, value):
        if not 1 <= value <= 240:
            raise serializers.ValidationError("Between 1 and 240 minutes.")
        return value

    def validate(self, attrs):
        start = attrs.get("office_start", getattr(self.instance, "office_start", datetime.time(8, 0)))
        end = attrs.get("office_end", getattr(self.instance, "office_end", datetime.time(17, 0)))
        if end <= start:
            raise serializers.ValidationError({"office_end": "Office hours must end after they start."})
        return attrs


class SupportSettingsView(APIView):
    """GET /api/support/settings/ (staff with Support Tickets) and PATCH (Admin)."""

    permission_classes = [permissions.IsAuthenticated, IsStaffMember]

    def get(self, request):
        return Response(SupportSettingsSerializer(SupportSettings.load()).data)

    def patch(self, request):
        if request.user.role != request.user.Role.ADMIN:
            return Response({"detail": "Only an Admin can change the support settings."}, status=403)
        obj = SupportSettings.load()
        ser = SupportSettingsSerializer(obj, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(ser.data)
