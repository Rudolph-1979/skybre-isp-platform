from django.contrib import admin
from .models import Job, Shift


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = ["title", "customer", "job_type", "status", "assigned_to", "start", "end"]
    list_filter = ["job_type", "status", "assigned_to"]
    search_fields = ["title", "description", "customer__full_name"]


@admin.register(Shift)
class ShiftAdmin(admin.ModelAdmin):
    list_display = ["staff", "start", "end", "status", "role_note"]
    list_filter = ["status", "staff"]
