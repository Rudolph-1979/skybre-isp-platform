import copy

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from .models import Lead, LeadNote


class LeadNoteSerializer(serializers.ModelSerializer):
    author_name = serializers.CharField(source="author_label", read_only=True)
    kind_display = serializers.CharField(source="get_kind_display", read_only=True)

    class Meta:
        model = LeadNote
        fields = ["id", "lead", "kind", "kind_display", "body", "author", "author_name", "created_at"]
        read_only_fields = ["id", "author", "author_name", "created_at"]


class LeadSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    source_display = serializers.CharField(source="get_source_display", read_only=True)
    lost_reason_display = serializers.CharField(source="get_lost_reason_display", read_only=True)
    partner_name = serializers.CharField(source="partner.name", read_only=True, default="")
    assigned_to_name = serializers.SerializerMethodField()
    tariff_name = serializers.CharField(source="interested_tariff.name", read_only=True, default="")
    customer_name = serializers.CharField(source="customer.full_name", read_only=True, default="")
    customer_reference = serializers.CharField(source="customer.customer_id", read_only=True, default="")
    value = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    follow_up_is_due = serializers.BooleanField(read_only=True)
    note_count = serializers.IntegerField(source="lead_notes.count", read_only=True)

    class Meta:
        model = Lead
        fields = [
            "id", "full_name", "company_name", "email", "phone",
            "address", "city", "zip_code",
            "source", "source_display", "source_detail",
            "partner", "partner_name",
            "assigned_to", "assigned_to_name",
            "interested_tariff", "tariff_name", "estimated_monthly_value", "value",
            "status", "status_display", "lost_reason", "lost_reason_display",
            "next_follow_up", "follow_up_is_due",
            "customer", "customer_name", "customer_reference",
            "notes", "note_count",
            "created_at", "updated_at", "closed_at",
        ]
        read_only_fields = ["id", "customer", "created_at", "updated_at", "closed_at"]

    def get_assigned_to_name(self, obj):
        if not obj.assigned_to_id or not obj.assigned_to:
            return ""
        user = obj.assigned_to
        full = f"{user.first_name} {user.last_name}".strip()
        return full or user.username

    def validate(self, attrs):
        """Run the model's own rules, so the API and the shell agree.

        DRF does not call Model.clean(), so without this the lost-reason
        rule would hold for a management command and quietly not hold for
        the screen everybody actually uses.

        Validated against a COPY with the incoming changes applied, not
        against `attrs` alone -- a PATCH that sets status to Lost without
        touching lost_reason has to be judged on the reason already
        stored, not on its absence from the request body.
        """
        candidate = copy.copy(self.instance) if self.instance is not None else Lead()
        for field, value in attrs.items():
            setattr(candidate, field, value)
        try:
            candidate.clean()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict)
        return attrs
