from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    AuditEventViewSet,
    CustomerHistoryView,
    CustomerSessionsView,
    sign_out,
)

router = DefaultRouter()
router.register("audit-events", AuditEventViewSet, basename="auditevent")

urlpatterns = [
    path("customers/<int:pk>/history/", CustomerHistoryView.as_view(), name="customer_history"),
    path("customers/<int:pk>/sessions/", CustomerSessionsView.as_view(), name="customer_sessions"),
    path("sign-out/", sign_out, name="sign_out"),
    *router.urls,
]
