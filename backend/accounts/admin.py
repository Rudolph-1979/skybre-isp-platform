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
    readonly_fields = ("secret", "created_at", "confirmed_at")
