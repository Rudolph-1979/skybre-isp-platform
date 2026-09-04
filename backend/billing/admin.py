from django.contrib import admin
from .models import Tariff, Service, Invoice, InvoiceItem, Payment, CreditRequest


@admin.register(Tariff)
class TariffAdmin(admin.ModelAdmin):
    list_display = ("name", "service_type", "price", "billing_period", "is_active")


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ("customer", "tariff", "status", "start_date")
    list_filter = ("status",)
    # Same reasoning as network.DeviceAdmin: ServiceSerializer makes this
    # write_only with a radius_password_set boolean, and the admin's
    # default form rendered the subscriber's actual RADIUS password as
    # editable plain text. It is set from the Services page.
    exclude = ("radius_password",)


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

    # Read-only on purpose. Every ledger effect of a payment (the
    # customer's balance, the invoice's paid_amount and Paid flip, and the
    # reversal of all three) lives in PaymentSerializer/PaymentViewSet, so
    # a payment added, edited or deleted through the admin's default forms
    # moves money in the payments table without moving it in the ledger.
    # Payments are recorded and corrected on the Finance page.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CreditRequest)
class CreditRequestAdmin(admin.ModelAdmin):
    list_display = ("customer", "amount", "status", "requested_by", "decided_by", "created_at")
    list_filter = ("status",)
