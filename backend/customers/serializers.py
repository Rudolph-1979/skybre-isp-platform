from rest_framework import serializers
from .models import Customer


class CustomerSerializer(serializers.ModelSerializer):
    assigned_staff_name = serializers.CharField(source="assigned_staff.username", read_only=True, default=None)

    class Meta:
        model = Customer
        fields = [
            "id", "customer_id", "customer_type", "category", "full_name", "company_name",
            "email", "phone", "address", "city", "zip_code", "status", "balance",
            "assigned_staff", "assigned_staff_name", "notes", "created_at", "updated_at", "user",
        ]
        read_only_fields = ["id", "customer_id", "created_at", "updated_at"]
