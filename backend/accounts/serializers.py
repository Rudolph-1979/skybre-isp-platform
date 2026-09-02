from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from .models import User, STAFF_ROLES
from . import two_factor


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = [
            "id", "username", "email", "first_name", "last_name", "role", "phone", "is_active",
            "allowed_sections", "allowed_partners", "visible_partners",
        ]
        # allowed_sections/allowed_partners are read-only here -- those are
        # only ever written through StaffPermissionsSerializer (Admin-only).
        # visible_partners IS self-service, but it's written through
        # accounts.views.MeView.patch's own hand-checked path (not this
        # serializer's validated_data), so it stays read-only here too --
        # this serializer just needs to expose the *current* user's own
        # values so the frontend can filter its nav/Customers view on
        # login/refresh.
        read_only_fields = ["id", "role", "allowed_sections", "allowed_partners", "visible_partners"]


class StaffPermissionsSerializer(serializers.ModelSerializer):
    """Admin-only view: lets an admin see and set which sections (and now,
    which reseller partners' customers) another staff member can access.
    Deliberately narrow -- everything except allowed_sections/
    allowed_partners is read-only here, since this endpoint's only job is
    permission management, not general user editing."""

    class Meta:
        model = User
        fields = ["id", "username", "first_name", "last_name", "role", "is_active", "allowed_sections", "allowed_partners"]
        read_only_fields = ["id", "username", "first_name", "last_name", "role", "is_active"]

    def validate_allowed_sections(self, value):
        valid_keys = set(User.Section.values)
        invalid = [v for v in value if v not in valid_keys]
        if invalid:
            raise serializers.ValidationError(f"Unknown section(s): {', '.join(invalid)}")
        return value

    def validate_allowed_partners(self, value):
        from customers.models import Partner  # local import: avoids a hard cross-app import at module load time

        valid_ids = set(Partner.objects.values_list("id", flat=True))
        invalid = [v for v in value if v not in valid_ids]
        if invalid:
            raise serializers.ValidationError(f"Unknown partner id(s): {', '.join(str(v) for v in invalid)}")
        return value


class StaffAccountsSerializer(serializers.ModelSerializer):
    """Admin-only account lifecycle management: create a new staff login,
    edit their basic details/role, and suspend/reactivate via is_active.
    Permanent deletion is handled by the viewset's destroy(), not here.
    Deliberately separate from StaffPermissionsSerializer (which only
    touches allowed_sections) -- this one owns everything about the
    account itself except section access."""

    # Write-only and optional: required to create a new account (enforced
    # in validate() below, since "required" alone would also demand it on
    # every edit) but left blank on update to leave the password unchanged.
    password = serializers.CharField(write_only=True, required=False, min_length=8, style={"input_type": "password"})

    class Meta:
        model = User
        fields = ["id", "username", "password", "first_name", "last_name", "email", "phone", "role", "is_active"]
        read_only_fields = ["id"]

    def validate_role(self, value):
        if value not in STAFF_ROLES:
            raise serializers.ValidationError(f"Role must be one of: {', '.join(STAFF_ROLES)}")
        return value

    def validate_username(self, value):
        qs = User.objects.filter(username=value)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("This username is already taken.")
        return value

    def validate(self, attrs):
        # A new account needs SOMETHING to get in with: either a password set
        # here, or an email address to send an invite to. Requiring a password
        # unconditionally -- which is what this used to do -- forced the admin
        # to invent one and then get it to the person somehow, which is a
        # password chosen by the wrong person sitting somewhere it shouldn't.
        if self.instance is None and not attrs.get("password") and not attrs.get("email"):
            raise serializers.ValidationError({
                "email": "Give an email address so they can be sent an invite, or set a password here."
            })
        return attrs

    def create(self, validated_data):
        password = validated_data.pop("password", None)
        user = User(**validated_data)
        if password:
            user.set_password(password)
        else:
            # No usable password at all until they set one from the invite, so
            # the account cannot be signed into in the meantime -- not even
            # with a guessable default.
            user.set_unusable_password()
        user.save()
        return user

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if password:
            instance.set_password(password)
        instance.save()
        return instance


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    # Not part of simplejwt's default fields — declared explicitly so it's
    # actually included in `attrs` for validate() to see. Optional: most
    # logins won't have 2FA enabled and never send this.
    totp_code = serializers.CharField(required=False, allow_blank=True, write_only=True)

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["role"] = user.role
        token["username"] = user.username
        return token

    def validate(self, attrs):
        # super().validate() checks username/password first and raises
        # AuthenticationFailed on bad credentials — so a wrong password
        # never reveals whether 2FA is enabled for that account.
        code = attrs.pop("totp_code", "")
        data = super().validate(attrs)

        device = getattr(self.user, "two_factor", None)
        if device is not None and device.confirmed:
            if not code:
                raise serializers.ValidationError(
                    {"code": "two_factor_required", "detail": "Enter your 6-digit authentication code."}
                )
            if not two_factor.verify_code(device, code):
                raise serializers.ValidationError(
                    {"code": "invalid_two_factor_code", "detail": "Invalid authentication code."}
                )

        data["user"] = UserSerializer(self.user).data
        customer_profile = getattr(self.user, "customer_profile", None)
        if customer_profile is not None:
            data["customer_id"] = customer_profile.id
        return data
