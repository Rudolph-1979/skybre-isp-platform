from django.contrib import admin
from .models import Job, Shift


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    # list_display can't show a ManyToManyField directly (Django admin has
    # no column renderer for one), hence the small method below -- same
    # information, just joined into one string for the changelist.
    list_display = ["title", "customer", "job_type", "status", "technicians_display", "start", "end"]
    list_filter = ["job_type", "status", "assigned_technicians"]
    search_fields = ["title", "description", "customer__full_name"]

    @admin.display(description="Assigned to")
    def technicians_display(self, obj):
        return ", ".join(u.first_name or u.username for u in obj.assigned_technicians.all()) or "—"


@admin.register(Shift)
class ShiftAdmin(admin.ModelAdmin):
    list_display = ["staff", "start", "end", "status", "role_note"]
    list_filter = ["status", "staff"]
