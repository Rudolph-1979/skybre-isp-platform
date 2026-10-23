from rest_framework.routers import DefaultRouter
from .views import TicketViewSet, TicketCommentViewSet

router = DefaultRouter()
router.register("tickets", TicketViewSet, basename="ticket")
router.register("ticket-comments", TicketCommentViewSet, basename="ticketcomment")

urlpatterns = router.urls
