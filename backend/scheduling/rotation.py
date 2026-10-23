"""Weekly support rotations (package #184, 16 Oct 2026).

Rudolph: "We have staff that is on weekly support rotating" ... "2 is on
vehicle support and one is on phone support".

Who is on in a given week is CALCULATED from the rotation (members, order,
people per week, first week) -- nothing is written per week. The only rows
are exceptions: a cover ("X does Y's week") or a swap (two covers).

API (all under /api/, Scheduling access):
  /support-rotations/                  list/create/update/delete rotations
  /support-rotations/schedule/?from=YYYY-MM-DD&to=YYYY-MM-DD
                                       every rotation's weeks touching the range
  /support-rotations/now/              this week and next, per rotation
  /support-rotations/<id>/cover/       {week_start, user, replacement, reason}
  /support-rotations/<id>/swap/        {week_start, user, with_user, reason}
  /support-rotations/<id>/overrides/<override_id>/   DELETE = undo (a swap undoes both sides)
"""
import datetime
import uuid

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from rest_framework import permissions, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from accounts.permissions import IsStaffMember, section_permission

from .models import SupportRotation, SupportRotationMember, SupportRotationOverride
from .serializers import staff_display_name

HasSchedulingAccess = section_permission("scheduling")
User = get_user_model()

# How far a swap looks ahead for the other person's own week.
SWAP_LOOKAHEAD_WEEKS = 52


# --- the arithmetic ----------------------------------------------------------

def week_index(rotation, week_start):
    return (week_start - rotation.first_week_start).days // 7


def week_start_for(rotation, day):
    """The start date of the rotation week that `day` (a date) falls in.

    Date-based: the handover TIME only matters for "who is on right now",
    which uses now_week_start() instead.
    """
    offset = (day - rotation.first_week_start).days % 7
    return day - datetime.timedelta(days=offset)


def handover_at(rotation, week_start):
    return timezone.make_aware(datetime.datetime.combine(week_start, rotation.handover_time))


def now_week_start(rotation, now=None):
    """The week on support at this moment, honouring the handover time:
    Monday 07:00 with an 08:00 handover is still last week."""
    now = timezone.localtime(now or timezone.now())
    start = week_start_for(rotation, now.date())
    if now < handover_at(rotation, start):
        start -= datetime.timedelta(days=7)
    return start


def planned_people(rotation, members, week_start):
    """Who the rotation order puts on in this week (before any cover/swap)."""
    if not members or week_start < rotation.first_week_start:
        return []
    k = min(max(rotation.people_per_week, 1), len(members))
    n = week_index(rotation, week_start)
    return [members[(n * k + i) % len(members)] for i in range(k)]


def _person(user, **extra):
    return {
        "id": user.pk,
        "name": staff_display_name(user),
        "full_name": (f"{user.first_name} {user.last_name}".strip() or user.username),
        "colour": user.display_colour,
        "phone": user.phone,
        **extra,
    }


def week_entry(rotation, members, overrides, week_start):
    """One rotation week: planned people with any cover/swap applied."""
    people = []
    for user in planned_people(rotation, members, week_start):
        o = overrides.get((week_start, user.pk))
        if o:
            people.append(_person(
                o.replacement, override_id=o.pk, instead_of=staff_display_name(user),
                kind="swap" if o.swap_group else "cover", reason=o.reason,
            ))
        else:
            people.append(_person(user))
    return {
        "rotation": rotation.pk,
        "rotation_name": rotation.name,
        "week_start": week_start.isoformat(),
        "week_end": (week_start + datetime.timedelta(days=7)).isoformat(),
        "starts_at": handover_at(rotation, week_start).isoformat(),
        "ends_at": handover_at(rotation, week_start + datetime.timedelta(days=7)).isoformat(),
        "people": people,
    }


def _load(rotation, start=None, end=None):
    members = [m.user for m in rotation.members.select_related("user").order_by("position", "id")]
    qs = rotation.overrides.select_related("replacement")
    if start:
        qs = qs.filter(week_start__gte=start)
    if end:
        qs = qs.filter(week_start__lte=end)
    overrides = {(o.week_start, o.user_id): o for o in qs}
    return members, overrides


def weeks_between(rotation, start, end):
    """Every rotation week that touches [start, end] (dates, inclusive)."""
    first = max(week_start_for(rotation, start), rotation.first_week_start)
    members, overrides = _load(rotation, first, end)
    out = []
    ws = first
    while ws <= end:
        out.append(week_entry(rotation, members, overrides, ws))
        ws += datetime.timedelta(days=7)
    return out


# --- serializers ---------------------------------------------------------------

class SupportRotationSerializer(serializers.ModelSerializer):
    # Write: the member user ids in rotation order. Read: the people.
    member_ids = serializers.ListField(child=serializers.IntegerField(), write_only=True, required=False)
    members = serializers.SerializerMethodField()

    class Meta:
        model = SupportRotation
        fields = [
            "id", "name", "people_per_week", "handover_weekday", "handover_time",
            "first_week_start", "is_active", "sort_order", "members", "member_ids",
        ]

    def get_members(self, obj):
        return [_person(m.user) for m in obj.members.select_related("user").order_by("position", "id")]

    def validate_people_per_week(self, value):
        if value < 1:
            raise serializers.ValidationError("At least one person per week.")
        return value

    def validate_handover_weekday(self, value):
        if value > 6:
            raise serializers.ValidationError("0 (Monday) to 6 (Sunday).")
        return value

    def validate_member_ids(self, ids):
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError("Someone is in the list twice.")
        found = {u.pk: u for u in User.objects.filter(pk__in=ids)}
        missing = [i for i in ids if i not in found]
        if missing:
            raise serializers.ValidationError(f"Unknown staff id(s): {missing}.")
        not_staff = [staff_display_name(found[i]) for i in ids if not found[i].is_staff_member]
        if not_staff:
            raise serializers.ValidationError(f"Not staff: {', '.join(not_staff)}.")
        return ids

    def validate(self, attrs):
        weekday = attrs.get("handover_weekday", getattr(self.instance, "handover_weekday", 0))
        first = attrs.get("first_week_start", getattr(self.instance, "first_week_start", None))
        if first and first.weekday() != weekday:
            names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            raise serializers.ValidationError(
                {"first_week_start": f"The first week must start on the handover day ({names[weekday]})."}
            )
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        ids = validated_data.pop("member_ids", [])
        rotation = super().create(validated_data)
        self._set_members(rotation, ids)
        return rotation

    @transaction.atomic
    def update(self, instance, validated_data):
        ids = validated_data.pop("member_ids", None)
        rotation = super().update(instance, validated_data)
        if ids is not None:
            self._set_members(rotation, ids)
        return rotation

    def _set_members(self, rotation, ids):
        rotation.members.all().delete()
        SupportRotationMember.objects.bulk_create(
            [SupportRotationMember(rotation=rotation, user_id=uid, position=i) for i, uid in enumerate(ids)]
        )


def _date(value, field):
    try:
        return datetime.date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise serializers.ValidationError({field: "A date, YYYY-MM-DD."})


def _staff(value, field):
    try:
        user = User.objects.get(pk=int(value))
    except (TypeError, ValueError, User.DoesNotExist):
        raise serializers.ValidationError({field: "Pick a staff member."})
    if not user.is_staff_member:
        raise serializers.ValidationError({field: "Not a staff member."})
    return user


# --- views ---------------------------------------------------------------------

class SupportRotationViewSet(viewsets.ModelViewSet):
    """Not partner-scoped, for the reason ShiftViewSet gives: a staff rota
    has no customer on it."""

    serializer_class = SupportRotationSerializer
    permission_classes = [permissions.IsAuthenticated, IsStaffMember, HasSchedulingAccess]
    pagination_class = None
    queryset = SupportRotation.objects.all()

    @action(detail=False, methods=["get"])
    def schedule(self, request):
        start = _date(request.query_params.get("from"), "from")
        end = _date(request.query_params.get("to"), "to")
        if end < start or (end - start).days > 120:
            raise serializers.ValidationError({"to": "Up to 120 days after 'from'."})
        weeks = []
        for rotation in SupportRotation.objects.filter(is_active=True):
            weeks.extend(weeks_between(rotation, start, end))
        return Response({"weeks": weeks})

    @action(detail=False, methods=["get"])
    def now(self, request):
        out = []
        for rotation in SupportRotation.objects.filter(is_active=True):
            ws = now_week_start(rotation)
            members, overrides = _load(rotation, ws, ws + datetime.timedelta(days=7))
            out.append({
                "rotation": rotation.pk,
                "rotation_name": rotation.name,
                "current": week_entry(rotation, members, overrides, ws),
                "next": week_entry(rotation, members, overrides, ws + datetime.timedelta(days=7)),
            })
        return Response({"rotations": out})

    def _planned(self, rotation, week_start):
        members, _ = _load(rotation)
        return members, [u.pk for u in planned_people(rotation, members, week_start)]

    def _check_week(self, rotation, week_start):
        if week_start_for(rotation, week_start) != week_start or week_start < rotation.first_week_start:
            raise serializers.ValidationError({"week_start": "Not the start of one of this rotation's weeks."})

    @action(detail=True, methods=["post"])
    def cover(self, request, pk=None):
        rotation = self.get_object()
        week_start = _date(request.data.get("week_start"), "week_start")
        self._check_week(rotation, week_start)
        user = _staff(request.data.get("user"), "user")
        replacement = _staff(request.data.get("replacement"), "replacement")
        _, planned = self._planned(rotation, week_start)
        if user.pk not in planned:
            raise serializers.ValidationError({"user": f"{staff_display_name(user)} is not on that week."})
        if replacement.pk == user.pk or replacement.pk in planned:
            raise serializers.ValidationError({"replacement": "Already on support that week."})
        if rotation.overrides.filter(week_start=week_start, user=user).exists():
            raise serializers.ValidationError({"user": "That week already has a cover or swap -- undo it first."})
        o = SupportRotationOverride.objects.create(
            rotation=rotation, week_start=week_start, user=user, replacement=replacement,
            reason=(request.data.get("reason") or "")[:255], created_by=request.user,
        )
        return Response({"override": o.pk}, status=201)

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def swap(self, request, pk=None):
        """User's week (week_start) goes to with_user; user takes with_user's
        next own week after it."""
        rotation = self.get_object()
        week_start = _date(request.data.get("week_start"), "week_start")
        self._check_week(rotation, week_start)
        user = _staff(request.data.get("user"), "user")
        other = _staff(request.data.get("with_user"), "with_user")
        members, planned = self._planned(rotation, week_start)
        if user.pk not in planned:
            raise serializers.ValidationError({"user": f"{staff_display_name(user)} is not on that week."})
        if other.pk in planned:
            raise serializers.ValidationError({"with_user": "Already on support that week."})
        if other.pk not in [m.pk for m in members]:
            raise serializers.ValidationError({"with_user": "Not in this rotation -- use Cover instead."})
        back = None
        for n in range(1, SWAP_LOOKAHEAD_WEEKS + 1):
            ws = week_start + datetime.timedelta(days=7 * n)
            ids = [u.pk for u in planned_people(rotation, members, ws)]
            if other.pk in ids and user.pk not in ids:
                back = ws
                break
        if back is None:
            raise serializers.ValidationError({"with_user": "No week found to swap back -- use Cover instead."})
        taken = rotation.overrides.filter(week_start=week_start, user=user).exists() or \
            rotation.overrides.filter(week_start=back, user=other).exists()
        if taken:
            raise serializers.ValidationError({"user": "One of those weeks already has a cover or swap -- undo it first."})
        group = str(uuid.uuid4())
        reason = (request.data.get("reason") or "")[:255]
        SupportRotationOverride.objects.create(
            rotation=rotation, week_start=week_start, user=user, replacement=other,
            reason=reason, swap_group=group, created_by=request.user,
        )
        SupportRotationOverride.objects.create(
            rotation=rotation, week_start=back, user=other, replacement=user,
            reason=reason, swap_group=group, created_by=request.user,
        )
        return Response({"swap_group": group, "swapped_back_week": back.isoformat()}, status=201)

    @action(detail=True, methods=["delete"], url_path=r"overrides/(?P<override_id>\d+)")
    def undo_override(self, request, pk=None, override_id=None):
        rotation = self.get_object()
        o = rotation.overrides.filter(pk=override_id).first()
        if not o:
            return Response({"detail": "Not found."}, status=404)
        if o.swap_group:
            rotation.overrides.filter(swap_group=o.swap_group).delete()
        else:
            o.delete()
        return Response(status=204)
