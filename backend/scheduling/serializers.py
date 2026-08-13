from rest_framework import serializers
from .models import Job, Shift


class JobSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source="customer.full_name", read_only=True, default=None)
    assigned_to_name = serializers.CharField(source="assigned_to.username", read_only=True, default=None)
    ticket_number = serializers.CharField(source="ticket.ticket_number", read_only=True, default=None)

    class Meta:
        model = Job
        fields = [
            "id", "customer", "customer_name", "ticket", "ticket_number",
            "assigned_to", "assigned_to_name", "job_type", "title", "description",
            "status", "start", "end", "location", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate(self, attrs):
        start = attrs.get("start", getattr(self.instance, "start", None))
        end = attrs.get("end", getattr(self.instance, "end", None))
        if start and end and end <= start:
            raise serializers.ValidationError("End time must be after the start time.")
        return attrs


class ShiftSerializer(serializers.ModelSerializer):
    staff_name = serializers.CharField(source="staff.username", read_only=True)

    class Meta:
        model = Shift
        fields = ["id", "staff", "staff_name", "start", "end", "role_note", "status", "notes", "created_at"]
        read_only_fields = ["id", "created_at"]

    def validate(self, attrs):
        start = attrs.get("start", getattr(self.instance, "start", None))
        end = attrs.get("end", getattr(self.instance, "end", None))
        if start and end and end <= start:
            raise serializers.ValidationError("End time must be after the start time.")
        return attrs
