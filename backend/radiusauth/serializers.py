from django.utils import timezone
from rest_framework import serializers
from .models import SpeedWindow, RadiusNasClient, RadAcct, OvpnSettings, OvpnClientConnection


class RadiusNasClientSerializer(serializers.ModelSerializer):
    # Same write-only-secret pattern used elsewhere: accept the shared
    # secret on create/update, never echo it back; `secret_set` tells the
    # frontend one is on file without exposing the value.
    secret = serializers.CharField(write_only=True, required=False, allow_blank=True)
    secret_set = serializers.SerializerMethodField()

    class Meta:
        model = RadiusNasClient
        fields = [
            "id", "name", "ip_address", "shortname", "secret", "secret_set",
            "realm", "description", "is_active", "created_at",
        ]
        read_only_fields = ["id", "created_at"]

    def get_secret_set(self, obj):
        return bool(obj.secret)

    def update(self, instance, validated_data):
        # Blank/omitted secret on an edit means "keep the existing one" --
        # only overwrite when staff actually typed a new value.
        if "secret" in validated_data and not validated_data["secret"]:
            validated_data.pop("secret")
        return super().update(instance, validated_data)


class OvpnSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = OvpnSettings
        fields = ["freeradius_ip", "notes", "updated_at"]
        read_only_fields = ["updated_at"]


class OvpnClientConnectionSerializer(serializers.ModelSerializer):
    # Same write-only-secret/`_set` companion-field pattern as
    # RadiusNasClientSerializer.secret.
    password = serializers.CharField(write_only=True, required=False, allow_blank=True)
    password_set = serializers.SerializerMethodField()

    class Meta:
        model = OvpnClientConnection
        fields = [
            "id", "name", "comment", "remote_ip", "remote_port", "username",
            "password", "password_set", "routes", "is_enabled", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_password_set(self, obj):
        return bool(obj.password)

    def update(self, instance, validated_data):
        # Blank/omitted password on an edit means "keep the existing
        # one" -- only overwrite when staff actually typed a new value.
        if "password" in validated_data and not validated_data["password"]:
            validated_data.pop("password")
        return super().update(instance, validated_data)


class RadAcctSerializer(serializers.ModelSerializer):
    is_active = serializers.BooleanField(read_only=True)
    # Resolved from RadiusNasClient.realm by matching this session's
    # nasipaddress -- not a column on radacct itself (that table stays a
    # verbatim mirror of FreeRADIUS's own schema). None if this NAS's IP
    # doesn't match any known client, or that client has no realm set.
    # RadAcctViewSet passes a precomputed {ip: realm} dict via serializer
    # context so this doesn't run one query per row.
    realm = serializers.SerializerMethodField()
    # How long the session has ACTUALLY been up.
    #
    # acctsessiontime is only written on an Interim-Update or an
    # Accounting-Stop, so on a router with interim-update=5m a live session
    # reports 0 for its first five minutes and then jumps. For a session that
    # is still open the honest answer is now minus its start, which needs
    # nothing from the NAS and is right at every instant.
    duration_seconds = serializers.SerializerMethodField()
    # Byte counters read from the ROUTER, when a live reader happens to be
    # running for that device (see network.live_broker). Accurate to the
    # second, where the accounting counters below lag by up to the interim
    # interval. null when no fresh router reading exists.
    live_input_octets = serializers.SerializerMethodField()
    live_output_octets = serializers.SerializerMethodField()

    class Meta:
        model = RadAcct
        fields = [
            "radacctid", "username", "nasipaddress", "realm", "framedipaddress", "callingstationid",
            "acctstarttime", "acctupdatetime", "acctstoptime", "acctsessiontime",
            "acctinputoctets", "acctoutputoctets", "acctterminatecause", "is_active",
            "duration_seconds", "live_input_octets", "live_output_octets",
        ]

    def get_duration_seconds(self, obj):
        if obj.acctstoptime and obj.acctstarttime:
            return int((obj.acctstoptime - obj.acctstarttime).total_seconds())
        if obj.acctstarttime:
            return int((timezone.now() - obj.acctstarttime).total_seconds())
        # Falls back to whatever the NAS last reported, which is better than
        # nothing for a row with no start time at all.
        return obj.acctsessiontime

    def _router(self, obj):
        return self.context.get("router_rates", {}).get(obj.username)

    def get_live_input_octets(self, obj):
        row = self._router(obj)
        return row["last_rx_byte"] if row else None

    def get_live_output_octets(self, obj):
        row = self._router(obj)
        return row["last_tx_byte"] if row else None

    def get_realm(self, obj):
        return self.context.get("realm_by_ip", {}).get(obj.nasipaddress)


class SpeedWindowSerializer(serializers.ModelSerializer):
    tariff_name = serializers.CharField(source="tariff.name", read_only=True, default=None)
    # Rendered here rather than in the frontend so the "22:00–06:00 runs
    # through midnight" reading is stated once, by the code that owns the
    # rule, instead of being re-derived by every screen that shows it.
    spans_midnight = serializers.SerializerMethodField()

    class Meta:
        model = SpeedWindow
        fields = [
            "id", "name", "tariff", "tariff_name", "start_time", "end_time",
            "weekdays", "speed_pct", "counts_toward_fup", "spans_midnight",
            "is_active", "created_at",
        ]
        read_only_fields = ["id", "created_at"]

    def get_spans_midnight(self, obj):
        return obj.start_time > obj.end_time

    def validate(self, attrs):
        start = attrs.get("start_time", getattr(self.instance, "start_time", None))
        end = attrs.get("end_time", getattr(self.instance, "end_time", None))
        if start is not None and end is not None and start == end:
            raise serializers.ValidationError(
                {"end_time": "Start and end are the same, so the window would never be on. "
                             "For all day, use 00:00 to 23:59."}
            )
        for day in attrs.get("weekdays", []) or []:
            if day > 6:
                raise serializers.ValidationError({"weekdays": "Days are 0 (Monday) to 6 (Sunday)."})
        return attrs
