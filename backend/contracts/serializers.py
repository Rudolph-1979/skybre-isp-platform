from rest_framework import serializers

from .models import Contract, ContractEvent, ContractSettings, ContractTemplate
from .services import ContractError, fill


class ContractTemplateSerializer(serializers.ModelSerializer):
    contract_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = ContractTemplate
        fields = ["id", "name", "title", "body_html", "is_active", "contract_count", "created_at", "updated_at"]
        read_only_fields = ["created_at", "updated_at"]

    def validate_body_html(self, value):
        try:
            fill(value, {})
        except ContractError as exc:
            raise serializers.ValidationError(str(exc))
        return value


class ContractSettingsSerializer(serializers.ModelSerializer):
    default_template_name = serializers.CharField(source="default_template.name", read_only=True, default="")

    class Meta:
        model = ContractSettings
        fields = ["default_template", "default_template_name", "auto_send_new_client",
                  "auto_send_lead_accepted", "link_valid_days"]

    def validate_link_valid_days(self, value):
        if not 1 <= value <= 90:
            raise serializers.ValidationError("Between 1 and 90 days.")
        return value

    def validate(self, attrs):
        template = attrs.get("default_template", getattr(self.instance, "default_template", None))
        auto = any(attrs.get(k, getattr(self.instance, k, False))
                   for k in ("auto_send_new_client", "auto_send_lead_accepted"))
        if auto and (template is None or not template.is_active):
            raise serializers.ValidationError(
                {"default_template": "Choose the template to send before switching automatic sending on."}
            )
        return attrs


def _name(user):
    if user is None:
        return ""
    return user.get_full_name() or user.username


class ContractEventSerializer(serializers.ModelSerializer):
    kind_display = serializers.CharField(source="get_kind_display", read_only=True)
    user_name = serializers.SerializerMethodField()

    class Meta:
        model = ContractEvent
        fields = ["id", "kind", "kind_display", "at", "user_name", "ip", "user_agent", "detail"]

    def get_user_name(self, obj):
        return _name(obj.user)


class ContractSerializer(serializers.ModelSerializer):
    reference = serializers.CharField(read_only=True)
    status_display = serializers.SerializerMethodField()
    kind_display = serializers.CharField(source="get_kind_display", read_only=True)
    is_expired = serializers.BooleanField(read_only=True)
    template_name = serializers.CharField(source="template.name", read_only=True, default="")
    created_by_name = serializers.SerializerMethodField()
    witnessed_by_name = serializers.SerializerMethodField()
    customer_name = serializers.CharField(source="customer.full_name", read_only=True)
    customer_code = serializers.CharField(source="customer.customer_id", read_only=True)
    has_signed_pdf = serializers.SerializerMethodField()

    class Meta:
        model = Contract
        fields = [
            "id", "reference", "customer", "customer_name", "customer_code", "kind", "kind_display",
            "template", "template_name", "lead", "title", "status", "status_display", "is_expired",
            "document_filename", "document_sha256", "sent_to", "sent_at", "expires_at", "viewed_at",
            "signed_at", "signer_name", "signer_id_number", "signer_ip", "signer_user_agent",
            "signed_in_person", "witnessed_by_name", "signed_sha256", "has_signed_pdf",
            "created_by_name", "created_at",
        ]
        read_only_fields = fields

    def get_status_display(self, obj):
        if obj.is_expired:
            return "Link expired"
        return obj.get_status_display()

    def get_created_by_name(self, obj):
        return _name(obj.created_by)

    def get_witnessed_by_name(self, obj):
        return _name(obj.witnessed_by)

    def get_has_signed_pdf(self, obj):
        return bool(obj.signed_sha256)


class ContractDetailSerializer(ContractSerializer):
    events = ContractEventSerializer(many=True, read_only=True)

    class Meta(ContractSerializer.Meta):
        fields = ContractSerializer.Meta.fields + ["events"]
        read_only_fields = fields
