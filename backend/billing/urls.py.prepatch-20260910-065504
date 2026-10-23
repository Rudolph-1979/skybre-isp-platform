from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import (
    TariffViewSet, ServiceViewSet, InvoiceViewSet, PaymentViewSet, CreditRequestViewSet,
    InvoiceDeletionRequestViewSet,
    PaymentMethodViewSet, BillingDefaultsView, ApplyBillingDefaultsView, ReminderSettingsView,
    SuspensionSettingsView, CustomerBillingConfigView, RecurringBillingRunViewSet, RecurringBillingViewSet,
    UpcomingBlocksView,
)

router = DefaultRouter()
router.register("tariffs", TariffViewSet, basename="tariff")
router.register("services", ServiceViewSet, basename="service")
router.register("invoices", InvoiceViewSet, basename="invoice")
router.register("payments", PaymentViewSet, basename="payment")
router.register("credit-requests", CreditRequestViewSet, basename="creditrequest")
router.register("invoice-deletion-requests", InvoiceDeletionRequestViewSet, basename="invoicedeletionrequest")
router.register("payment-methods", PaymentMethodViewSet, basename="paymentmethod")
router.register("recurring-billing-runs", RecurringBillingRunViewSet, basename="recurringbillingrun")
router.register("recurring-billing", RecurringBillingViewSet, basename="recurringbilling")

urlpatterns = router.urls + [
    path("billing-defaults/", BillingDefaultsView.as_view(), name="billing-defaults"),
    path("billing-defaults/apply-to-existing/", ApplyBillingDefaultsView.as_view(), name="billing-defaults-apply"),
    path("reminder-settings/", ReminderSettingsView.as_view(), name="reminder-settings"),
    path("suspension-settings/", SuspensionSettingsView.as_view(), name="suspension-settings"),
    path("customer-billing-config/<int:customer_id>/", CustomerBillingConfigView.as_view(), name="customer-billing-config"),
    path("upcoming-blocks/", UpcomingBlocksView.as_view(), name="upcoming-blocks"),
]
