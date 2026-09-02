from rest_framework import serializers

from .models import AuditEvent


class AuditEventSerializer(serializers.ModelSerializer):
    action_display = serializers.CharField(source="get_action_display", read_only=True)
    # The snapshot, always -- never the live actor. See AuditEvent's
    # docstring: chasing the FK would rewrite history every time somebody
    # is renamed, and would say "(deleted)" for the accounts most worth
    # reading about.
    actor_name = serializers.CharField(source="actor_label", read_only=True)
    customer_name = serializers.SerializerMethodField()

    class Meta:
        model = AuditEvent
        fields = [
            "id", "action", "action_display", "actor", "actor_name",
            "target_type", "target_id", "target_label", "customer",
            "customer_name", "changes", "detail", "ip_address",
            "created_at",
        ]
        read_only_fields = fields

    def get_customer_name(self, obj):
        return obj.customer.full_name if obj.customer_id and obj.customer else ""


class CustomerSessionSerializer(serializers.Serializer):
    """One RADIUS session, from the customer's point of view.

    Deliberately not RadAcctSerializer. That one serves the Networking
    live-sessions screen and speaks in RADIUS terms (nasipaddress,
    acctinputoctets, realm). The people reading a customer's session log
    are answering "was he online last night", so this speaks in those
    terms instead, and drops the NAS-side detail that only means
    something to the network team.
    """

    session_id = serializers.CharField()
    started_at = serializers.DateTimeField(allow_null=True)
    ended_at = serializers.DateTimeField(allow_null=True)
    duration_seconds = serializers.IntegerField(allow_null=True)
    ip_address = serializers.CharField(allow_null=True, allow_blank=True)
    download_bytes = serializers.IntegerField()
    upload_bytes = serializers.IntegerField()
    terminate_cause = serializers.CharField(allow_blank=True)
    username = serializers.CharField()
    active = serializers.BooleanField()
