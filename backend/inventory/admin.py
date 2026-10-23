from django.contrib import admin
from .models import (
    Supplier,
    StockSite,
    Product,
    SerializedUnit,
    StockReceipt,
    StockReceiptLine,
    StockIssue,
    StockIssueLine,
    StockMovement,
)


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ["name", "contact_person", "phone", "email"]
    search_fields = ["name", "contact_person", "email"]


@admin.register(StockSite)
class StockSiteAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "site_type", "vehicle", "is_default", "is_active"]
    list_filter = ["site_type", "is_active", "is_default"]
    search_fields = ["name", "code", "vehicle__registration_number"]


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ["name", "sku", "category", "tracking_type", "quantity_on_hand", "is_active"]
    list_filter = ["category", "tracking_type", "is_active"]
    search_fields = ["name", "sku"]


@admin.register(SerializedUnit)
class SerializedUnitAdmin(admin.ModelAdmin):
    list_display = ["serial_number", "mac_address", "product", "status"]
    list_filter = ["status", "product"]
    search_fields = ["serial_number", "mac_address"]


class StockReceiptLineInline(admin.TabularInline):
    model = StockReceiptLine
    extra = 0


@admin.register(StockReceipt)
class StockReceiptAdmin(admin.ModelAdmin):
    list_display = ["invoice_number", "supplier", "invoice_date", "received_by", "created_at"]
    list_filter = ["supplier"]
    search_fields = ["invoice_number"]
    inlines = [StockReceiptLineInline]


class StockIssueLineInline(admin.TabularInline):
    model = StockIssueLine
    extra = 0


@admin.register(StockIssue)
class StockIssueAdmin(admin.ModelAdmin):
    list_display = ["id", "job", "issued_to", "issued_at"]
    list_filter = ["issued_to"]
    inlines = [StockIssueLineInline]


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ["product", "movement_type", "quantity", "created_at"]
    list_filter = ["movement_type", "product"]
