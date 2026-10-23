from django.urls import path
from rest_framework.routers import DefaultRouter
from .support_views import SupportDashboardView, SupportSettingsView
from .views import TicketViewSet, TicketCommentViewSet

router = DefaultRouter()
router.register("tickets", TicketViewSet, basename="ticket")
router.register("ticket-comments", TicketCommentViewSet, basename="ticketcomment")

urlpatterns = router.urls + [
    # The Support page (#212).
    path("support/dashboard/", SupportDashboardView.as_view(), name="support-dashboard"),
    path("support/settings/", SupportSettingsView.as_view(), name="support-settings"),
]
