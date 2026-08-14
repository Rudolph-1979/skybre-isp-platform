from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    EmailTemplateViewSet, EmailLogViewSet,
    EmailPreviewView, SendCustomerEmailView, BulkEmailView,
)

router = DefaultRouter()
router.register("email-templates", EmailTemplateViewSet, basename="email-template")
router.register("email-logs", EmailLogViewSet, basename="email-log")

urlpatterns = router.urls + [
    path("email-preview/", EmailPreviewView.as_view(), name="email-preview"),
    path("bulk-email/", BulkEmailView.as_view(), name="bulk-email"),
    path("customers/<int:customer_id>/send-email/", SendCustomerEmailView.as_view(), name="send-customer-email"),
]
