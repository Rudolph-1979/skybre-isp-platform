from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    EmailTemplateViewSet, EmailLogViewSet,
    EmailPreviewView, SendCustomerEmailView, BulkEmailView,
    EmailSettingsView, EmailSettingsTestView,
    InvoicePdfView, CustomerStatementPdfView,
)

router = DefaultRouter()
router.register("email-templates", EmailTemplateViewSet, basename="email-template")
router.register("email-logs", EmailLogViewSet, basename="email-log")

urlpatterns = router.urls + [
    path("email-preview/", EmailPreviewView.as_view(), name="email-preview"),
    path("bulk-email/", BulkEmailView.as_view(), name="bulk-email"),
    path("customers/<int:customer_id>/send-email/", SendCustomerEmailView.as_view(), name="send-customer-email"),
    # Document PDFs, readable by staff and by the owning customer -- see
    # _DocumentPdfMixin. Registered here rather than on the billing router
    # because they render the same file the emailer attaches.
    path("invoices/<int:pk>/pdf/", InvoicePdfView.as_view(), name="invoice-pdf"),
    path("customers/<int:customer_id>/statement/pdf/", CustomerStatementPdfView.as_view(), name="customer-statement-pdf"),
    path("email-settings/", EmailSettingsView.as_view(), name="email-settings"),
    path("email-settings/test/", EmailSettingsTestView.as_view(), name="email-settings-test"),
]
