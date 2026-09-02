from rest_framework import serializers

from .models import EmailTemplate, EmailLog, EmailSettings


class EmailTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmailTemplate
        fields = ["id", "key", "name", "subject", "body_html", "has_attachment", "updated_at"]
        read_only_fields = ["id", "key", "name", "has_attachment", "updated_at"]


class EmailLogSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source="customer.full_name", read_only=True, default=None)
    sent_by_name = serializers.CharField(source="sent_by.username", read_only=True, default=None)
    template_name = serializers.CharField(source="get_template_key_display", read_only=True)

    class Meta:
        model = EmailLog
        fields = [
            "id", "customer", "customer_name", "template_key", "template_name",
            "recipient_email", "subject", "status", "error_message",
            "sent_by", "sent_by_name", "batch_id", "created_at",
        ]
        read_only_fields = fields


class EmailSettingsSerializer(serializers.ModelSerializer):
    """Admin-only SMTP configuration. smtp_password is write-only and never
    echoed back — smtp_password_set tells the frontend whether one is
    currently stored, without ever exposing the value itself, the same
    write-only-secret pattern used for user account passwords elsewhere in
    this app. Every field is optional: leaving one blank/unset falls back
    to the server's .env-driven default (see notifications.email_settings)."""

    smtp_password = serializers.CharField(write_only=True, required=False, allow_blank=True)
    smtp_password_set = serializers.SerializerMethodField()

    class Meta:
        model = EmailSettings
        fields = [
            "smtp_host", "smtp_port", "smtp_username", "smtp_password", "smtp_password_set",
            "use_tls", "use_ssl", "default_from_email", "company_name", "site_url", "updated_at",
        ]
        read_only_fields = ["updated_at"]

    def get_smtp_password_set(self, obj) -> bool:
        return bool(obj.smtp_password)

    def update(self, instance, validated_data):
        # A blank/omitted password means "leave whatever's stored alone" --
        # only overwrite it when the admin actually typed a new value.
        password = validated_data.pop("smtp_password", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if password:
            instance.smtp_password = password
        instance.save()
        return instance
