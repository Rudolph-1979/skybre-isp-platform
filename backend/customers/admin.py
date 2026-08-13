from django.contrib import admin
from .models import Customer


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("customer_id", "full_name", "email", "status", "category", "balance", "created_at")
    search_fields = ("customer_id", "full_name", "email", "phone")
    list_filter = ("status", "category", "customer_type")
