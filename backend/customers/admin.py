from django.contrib import admin
from .models import Customer, CustomerTask


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("customer_id", "full_name", "email", "status", "category", "balance", "created_at")
    search_fields = ("customer_id", "full_name", "email", "phone")
    list_filter = ("status", "category", "customer_type")


@admin.register(CustomerTask)
class CustomerTaskAdmin(admin.ModelAdmin):
    list_display = ("title", "customer", "status", "priority", "due_date", "assigned_to", "created_at")
    search_fields = ("title", "description", "customer__full_name", "customer__customer_id")
    list_filter = ("status", "priority")
    autocomplete_fields = ("customer",)
