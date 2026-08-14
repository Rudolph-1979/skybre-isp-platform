from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from .models import User
from . import two_factor


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "username", "email", "first_name", "last_name", "role", "phone", "is_active"]
        read_only_fields = ["id", "role"]


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
