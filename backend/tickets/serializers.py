from rest_framework import serializers
from .models import Ticket, TicketComment


class TicketCommentSerializer(serializers.ModelSerializer):
    author_name = serializers.CharField(source="author.username", read_only=True, default=None)

    class Meta:
        model = TicketComment
        fields = ["id", "ticket", "author", "author_name", "message", "is_internal", "created_at"]
        read_only_fields = ["id", "created_at", "author"]


class TicketSerializer(serializers.ModelSerializer):
    comments = serializers.SerializerMethodField()
    customer_name = serializers.CharField(source="customer.full_name", read_only=True)
    assigned_to_name = serializers.CharField(source="assigned_to.username", read_only=True, default=None)

    class Meta:
        model = Ticket
        fields = [
            "id", "ticket_number", "customer", "customer_name", "subject", "description",
            "department", "status", "priority", "assigned_to", "assigned_to_name",
            "created_at", "updated_at", "comments",
        ]
        read_only_fields = ["id", "ticket_number", "created_at", "updated_at"]

    def get_comments(self, obj):
        user = self.context["request"].user if "request" in self.context else None
        qs = obj.comments.all()
        if not user or not user.is_staff_member:
            qs = qs.filter(is_internal=False)
        return TicketCommentSerializer(qs, many=True).data
