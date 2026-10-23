from rest_framework import serializers

from .models import Account


class AccountSerializer(serializers.ModelSerializer):
    type_display = serializers.CharField(source="get_type_display", read_only=True)
    is_system = serializers.BooleanField(read_only=True)
    # How many expenses are filed against this account. Surfaced so the UI
    # can explain WHY an account refuses to be deleted, rather than just
    # refusing -- "12 expenses use this" is actionable, "cannot delete" is
    # an argument with the software.
    in_use_count = serializers.SerializerMethodField()

    class Meta:
        model = Account
        fields = [
            "id", "code", "name", "type", "type_display", "description",
            "system_key", "is_system", "is_active", "in_use_count", "created_at",
        ]
        # system_key is the platform's own handle on an account. Writable,
        # somebody could point "the VAT account" at petty cash.
        read_only_fields = ["id", "system_key", "created_at"]

    def get_in_use_count(self, obj):
        return obj.expenses.count()

    def validate_code(self, value):
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("An account needs a code.")
        return value
