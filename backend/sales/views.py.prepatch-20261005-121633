from django.db.models import Count, Q, Sum
from django.utils import timezone
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsStaffMember, section_permission

from .models import Lead, LeadNote
from .serializers import LeadNoteSerializer, LeadSerializer

HasSalesAccess = section_permission("sales")


def scope_to_partners(qs, user):
    """Same reseller-visibility rule the customer list uses.

    A rep restricted to one reseller's customers must not see another
    reseller's pipeline. Leads with no partner are direct enquiries and
    belong to everybody, exactly as no-partner customers do.
    """
    allowed = getattr(user, "allowed_partners", None) or []
    if allowed and user.role != user.Role.ADMIN:
        return qs.filter(Q(partner_id__in=allowed) | Q(partner__isnull=True))
    return qs


class LeadViewSet(viewsets.ModelViewSet):
    serializer_class = LeadSerializer
    permission_classes = [permissions.IsAuthenticated, IsStaffMember, HasSalesAccess]
    filterset_fields = ["status", "source", "partner", "assigned_to", "lost_reason"]
    search_fields = [
        "full_name", "company_name", "email", "phone",
        "address", "city", "source_detail", "notes",
    ]
    ordering_fields = ["created_at", "next_follow_up", "full_name", "status"]
    ordering = ["-created_at"]

    def get_queryset(self):
        qs = Lead.objects.select_related(
            "partner", "assigned_to", "interested_tariff", "customer"
        ).all()
        qs = scope_to_partners(qs, self.request.user)
        params = self.request.query_params

        if params.get("open") == "true":
            qs = qs.filter(status__in=Lead.OPEN_STATUSES)

        if params.get("due") == "true":
            # Overdue counts as due. A rep needs one list; splitting
            # "today" from "late" makes the late one the one that stops
            # getting opened.
            qs = qs.filter(
                status__in=Lead.OPEN_STATUSES,
                next_follow_up__isnull=False,
                next_follow_up__lte=timezone.localdate(),
            )

        if params.get("mine") == "true":
            qs = qs.filter(assigned_to=self.request.user)

        return qs

    def perform_create(self, serializer):
        # An unassigned lead is one nobody is chasing. Default it to
        # whoever entered it -- they can hand it on, but it never starts
        # life belonging to no one.
        lead = serializer.save(
            assigned_to=serializer.validated_data.get("assigned_to") or self.request.user
        )
        LeadNote.objects.create(
            lead=lead,
            author=self.request.user,
            kind=LeadNote.Kind.SYSTEM,
            body=f"Lead created — {lead.get_source_display()}.",
        )

    def perform_update(self, serializer):
        before = serializer.instance.status
        lead = serializer.save()
        if lead.status != before:
            LeadNote.objects.create(
                lead=lead,
                author=self.request.user,
                kind=LeadNote.Kind.SYSTEM,
                body=(
                    f"Stage: {Lead.Status(before).label} → {lead.get_status_display()}"
                    + (f" ({lead.get_lost_reason_display()})" if lead.lost_reason else "")
                ),
            )

    @action(detail=True, methods=["post"])
    def convert(self, request, pk=None):
        """Turn this lead into a customer.

        Idempotent -- a second call returns the customer already linked
        rather than creating another. Two people clicking Convert within
        a few seconds of each other is an ordinary Tuesday, and a
        duplicate customer is expensive to unpick afterwards.
        """
        lead = self.get_object()
        already = bool(lead.customer_id)
        customer = lead.convert_to_customer(actor=request.user)
        return Response(
            {
                "customer_id": customer.pk,
                "customer_reference": customer.customer_id,
                "already_converted": already,
                "lead": LeadSerializer(lead).data,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["get", "post"], url_path="notes")
    def notes_action(self, request, pk=None):
        lead = self.get_object()
        if request.method == "GET":
            return Response(
                LeadNoteSerializer(lead.lead_notes.select_related("author"), many=True).data
            )
        serializer = LeadNoteSerializer(data={**request.data, "lead": lead.pk})
        serializer.is_valid(raise_exception=True)
        note = serializer.save(lead=lead, author=request.user)
        # Logging a call or an email usually means the next one has been
        # agreed. Offered as an optional field on the note rather than a
        # separate save, so it happens in the same keystroke or not at all.
        follow_up = request.data.get("next_follow_up")
        if follow_up:
            lead.next_follow_up = follow_up
            lead.save(update_fields=["next_follow_up", "updated_at"])
        return Response(LeadNoteSerializer(note).data, status=status.HTTP_201_CREATED)


class PipelineSummaryView(APIView):
    """Counts and value per stage, plus what is due today.

    One request rather than one per stage: the stage strip is the first
    thing on the page and six sequential round trips is how a fast screen
    becomes a slow one.
    """

    permission_classes = [permissions.IsAuthenticated, IsStaffMember, HasSalesAccess]

    def get(self, request):
        qs = scope_to_partners(Lead.objects.all(), request.user)
        if request.query_params.get("mine") == "true":
            qs = qs.filter(assigned_to=request.user)

        rows = {
            row["status"]: row
            for row in qs.values("status").annotate(
                count=Count("id"),
                value=Sum("estimated_monthly_value"),
            )
        }

        # Sum() above only covers the explicit override. The rest of the
        # pipeline's value comes from each lead's tariff, so the fallback
        # is applied in Python -- correct beats one query here, and the
        # row count is a pipeline, not a ledger.
        stages = []
        for choice in Lead.Status:
            leads = [lead for lead in qs.select_related("interested_tariff") if lead.status == choice.value]
            stages.append(
                {
                    "status": choice.value,
                    "label": choice.label,
                    "count": rows.get(choice.value, {}).get("count", 0),
                    "value": sum((lead.value for lead in leads), start=0),
                    "is_open": choice.value in Lead.OPEN_STATUSES,
                }
            )

        today = timezone.localdate()
        open_qs = qs.filter(status__in=Lead.OPEN_STATUSES)
        due = open_qs.filter(next_follow_up__isnull=False, next_follow_up__lte=today)

        return Response(
            {
                "stages": stages,
                "open_count": open_qs.count(),
                "open_value": sum(
                    (lead.value for lead in open_qs.select_related("interested_tariff")), start=0
                ),
                "due_count": due.count(),
                # Leads nobody has scheduled anything for. Not overdue --
                # invisible, which is worse, because an overdue list at
                # least admits they exist.
                "unscheduled_count": open_qs.filter(next_follow_up__isnull=True).count(),
            }
        )
