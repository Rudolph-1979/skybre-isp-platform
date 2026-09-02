from rest_framework import serializers

from .models import BankAccount, BankTransaction, BankFeedSyncLog


class BankAccountSerializer(serializers.ModelSerializer):
    """Admin-only. api_client_secret is write-only and never echoed back --
    api_client_secret_set tells the frontend whether one is currently
    stored, without ever exposing the value -- the same write-only-secret
    pattern used for EmailSettings.smtp_password / Service.radius_password
    elsewhere in this app."""

    api_client_secret = serializers.CharField(write_only=True, required=False, allow_blank=True)
    api_client_secret_set = serializers.SerializerMethodField()

    class Meta:
        model = BankAccount
        fields = [
            "id", "name", "account_number", "branch_code", "is_active",
            "api_base_url", "api_client_id", "api_client_secret", "api_client_secret_set",
            "last_synced_at", "last_sync_status", "last_sync_message", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "last_synced_at", "last_sync_status", "last_sync_message", "created_at", "updated_at"]

    def get_api_client_secret_set(self, obj):
        return obj.api_client_secret_set()

    def _save_with_optional_secret(self, instance, validated_data):
        # A blank/omitted secret means "leave whatever's stored alone" --
        # only overwrite it when the admin actually typed a new value.
        secret = validated_data.pop("api_client_secret", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if secret:
            instance.api_client_secret = secret
        instance.save()
        return instance

    def create(self, validated_data):
        secret = validated_data.pop("api_client_secret", "")
        instance = BankAccount(**validated_data)
        instance.api_client_secret = secret
        instance.save()
        return instance

    def update(self, instance, validated_data):
        return self._save_with_optional_secret(instance, validated_data)


class BankTransactionSerializer(serializers.ModelSerializer):
    account_name = serializers.CharField(source="account.name", read_only=True)
    matched_customer_name = serializers.CharField(source="matched_customer.full_name", read_only=True, default=None)
    matched_supplier_name = serializers.CharField(source="matched_supplier.name", read_only=True, default=None)
    confirmed_by_name = serializers.CharField(source="confirmed_by.username", read_only=True, default=None)

    class Meta:
        model = BankTransaction
        fields = [
            "id", "account", "account_name", "source", "external_id", "date", "description", "amount",
            "status", "matched_customer", "matched_customer_name",
            "matched_supplier", "matched_supplier_name", "match_method",
            "confirmed_by", "confirmed_by_name", "confirmed_at",
            "created_payment", "created_expense", "created_at",
        ]
        # Every field here is read-only -- state changes only ever happen
        # through the viewset's assign/confirm/ignore/unmatch actions, not
        # a generic PATCH, so each transition can enforce its own rules
        # (e.g. nothing can un-confirm a transaction that already became a
        # real Payment).
        read_only_fields = fields


class BankFeedSyncLogSerializer(serializers.ModelSerializer):
    account_name = serializers.CharField(source="account.name", read_only=True, default=None)
    triggered_by_name = serializers.CharField(source="triggered_by.username", read_only=True, default=None)

    class Meta:
        model = BankFeedSyncLog
        fields = [
            "id", "account", "account_name", "status", "status_message",
            "transactions_fetched", "transactions_new", "transactions_matched",
            "triggered_by", "triggered_by_name", "created_at",
        ]
        read_only_fields = fields
