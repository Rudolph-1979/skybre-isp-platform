from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = ("username", "email", "role", "is_active", "date_joined")
    fieldsets = UserAdmin.fieldsets + (("ISP Platform", {"fields": ("role", "phone")}),)
