from django.contrib import admin
from .models import Tariff, Service, Invoice, InvoiceItem, Payment, CreditRequest


@admin.register(Tariff)
class TariffAdmin(admin.ModelAdmin):
    list_display = ("name", "service_type", "price", "billing_period", "is_active")


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ("customer", "tariff", "status", "start_date")
    list_filter = ("status",)


class InvoiceItemInline(admin.TabularInline):
    model = InvoiceItem
    extra = 0


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("number", "customer", "status", "date_created", "date_due", "total", "paid_amount")
    list_filter = ("status",)
    inlines = [InvoiceItemInline]


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("customer", "invoice", "amount", "method", "date")
    list_filter = ("method",)


@admin.register(CreditRequest)
class CreditRequestAdmin(admin.ModelAdmin):
    list_display = ("customer", "amount", "status", "requested_by", "decided_by", "created_at")
    list_filter = ("status",)
