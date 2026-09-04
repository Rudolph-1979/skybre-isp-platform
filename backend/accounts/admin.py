from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User, TwoFactorAuth


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = ("username", "email", "role", "is_active", "date_joined")
    fieldsets = UserAdmin.fieldsets + (("ISP Platform", {"fields": ("role", "phone")}),)


@admin.register(TwoFactorAuth)
class TwoFactorAuthAdmin(admin.ModelAdmin):
    # Read-only visibility for support purposes (e.g. confirming who has
    # 2FA on) — never expose the secret itself here.
    list_display = ("user", "confirmed", "created_at", "confirmed_at")
    list_filter = ("confirmed",)
    search_fields = ("user__username",)
    # `exclude`, NOT readonly_fields. That comment above has been right all
    # along and the code contradicted it: in Django, readonly_fields means
    # "render this value as uneditable text", not "hide it". With no
    # fields/fieldsets declared the default form included `secret`, so
    # readonly_fields printed the base32 TOTP seed in plain text on the
    # change form.
    #
    # Anyone given view access for the stated purpose -- checking who has
    # 2FA on -- could therefore open an admin's row, read the seed, enter
    # it into any authenticator app, and generate a valid second factor
    # for that account indefinitely, with nothing about the device row
    # changing to show it had happened. This is the only place in the
    # platform the secret is readable back; the API surface returns it once
    # at setup by necessity and never again.
    exclude = ("secret",)
    readonly_fields = ("created_at", "confirmed_at")

    def has_add_permission(self, request):
        # A TOTP device is enrolled by its owner through the API, which is
        # what generates and confirms the secret. One created here would
        # have no usable secret and would lock the user out of their own
        # second factor.
        return False
