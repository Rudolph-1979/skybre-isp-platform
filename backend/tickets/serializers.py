from rest_framework import serializers
from .models import Ticket, TicketComment, TicketEscalation


class TicketEscalationSerializer(serializers.ModelSerializer):
    escalated_by_name = serializers.CharField(source="escalated_by.username", read_only=True, default=None)
    decided_by_name = serializers.CharField(source="decided_by.username", read_only=True, default=None)

    class Meta:
        model = TicketEscalation
        fields = [
            "id", "ticket", "escalated_by", "escalated_by_name", "reason", "escalated_at",
            "decision", "decided_by", "decided_by_name", "decided_at", "decision_note",
        ]
        # Every field here is set by TicketViewSet's escalate/decide-escalation
        # actions, never by a plain POST/PATCH to a serializer a client
        # could reach directly -- there is no registered viewset for this
        # model at all (see tickets.views), so read_only_fields is really
        # just documentation of intent rather than something enforced
        # elsewhere, but it's still the honest description of how this
        # data actually gets written.
        read_only_fields = fields


class TicketCommentSerializer(serializers.ModelSerializer):
    # One field for "who said this", whoever they are. The screen already
    # renders author_name, so a customer's reply arriving through the
    # Conecto app shows their name there without the frontend changing --
    # and external_author is exposed alongside it so the UI can mark those
    # differently when somebody gets to it.
    author_name = serializers.SerializerMethodField()

    def get_author_name(self, obj):
        if obj.external_author:
            return obj.external_author
        return obj.author.username if obj.author_id else None

    class Meta:
        model = TicketComment
        fields = [
            "id", "ticket", "author", "author_name", "external_author",
            "message", "is_internal", "created_at",
        ]
        # external_author is read-only through the API on purpose: it is set
        # by the Conecto webhook and nowhere else. A staff member able to
        # write it could post a comment under a customer's name.
        read_only_fields = ["id", "created_at", "author", "external_author"]


class TicketSerializer(serializers.ModelSerializer):
    comments = serializers.SerializerMethodField()
    # default=None because customer is NULL on an internal ticket. Without
    # it, DRF raises walking the source path and the whole list 500s the
    # first time a technician files feedback.
    customer_name = serializers.CharField(
        source="customer.full_name", read_only=True, default=None
    )
    assigned_to_name = serializers.CharField(source="assigned_to.username", read_only=True, default=None)
    created_by_name = serializers.CharField(source="created_by.username", read_only=True, default=None)
    accepted_by_name = serializers.CharField(source="accepted_by.username", read_only=True, default=None)
    escalations = TicketEscalationSerializer(many=True, read_only=True)
    # The one thing the UI actually needs to decide what buttons to show
    # (Escalate vs "awaiting NOC" vs Accept/Reject) without re-deriving it
    # from the full `escalations` list on every page -- the most recent
    # escalation still awaiting a decision, or null if none is pending.
    # Nesting the whole history above is for the ticket's activity trail;
    # this is for the ticket's current state.
    pending_escalation = serializers.SerializerMethodField()

    def get_pending_escalation(self, obj):
        pending = next(
            (e for e in obj.escalations.all() if e.decision == TicketEscalation.Decision.PENDING),
            None,
        )
        return TicketEscalationSerializer(pending).data if pending else None

    class Meta:
        model = Ticket
        fields = [
            "id", "ticket_number", "customer", "customer_name", "subject", "description",
            "department", "status", "priority", "assigned_to", "assigned_to_name",
            "created_by", "created_by_name", "created_at", "updated_at", "comments",
            "is_internal", "accepted_by", "accepted_by_name", "accepted_at", "resolved_at",
            "escalations", "pending_escalation", "disqualified_from_kpi",
        ]
        # is_internal is READ-ONLY, like external_author on comments and for
        # the same reason: it is set by the webhook that receives a
        # technician's job feedback and nowhere else. Writable, it would let
        # a staff member flip a customer's ticket to internal -- hiding a
        # conversation the customer was part of and, because of the check
        # constraint, failing at the database instead in a way nobody could
        # read.
        #
        # accepted_by/accepted_at/resolved_at are read-only for the same
        # shape of reason as is_internal: each is written by one specific,
        # deliberate action (TicketViewSet.accept, and the resolved-status
        # transition in perform_update) and nowhere else. Writable here, a
        # PATCH could backdate a ticket's acceptance or invent a resolution
        # date -- both of which feed the KPI/bonus report, so both need to
        # be facts the system recorded, not values a client asserted.
        read_only_fields = [
            "id", "ticket_number", "created_by", "created_at", "updated_at",
            "is_internal", "accepted_by", "accepted_at", "resolved_at",
            "disqualified_from_kpi",
        ]

    def get_comments(self, obj):
        user = self.context["request"].user if "request" in self.context else None
        qs = obj.comments.all()
        if not user or not user.is_staff_member:
            qs = qs.filter(is_internal=False)
        return TicketCommentSerializer(qs, many=True).data
