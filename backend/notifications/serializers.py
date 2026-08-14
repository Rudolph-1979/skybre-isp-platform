from rest_framework import serializers

from .models import EmailTemplate, EmailLog


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
