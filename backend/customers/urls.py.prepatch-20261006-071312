from rest_framework.routers import DefaultRouter
from .views import (
    CustomerViewSet, CustomerDeletionRequestViewSet, CustomerTaskViewSet, PartnerViewSet,
)

router = DefaultRouter()
router.register("customers", CustomerViewSet, basename="customer")
router.register("customer-deletion-requests", CustomerDeletionRequestViewSet, basename="customer-deletion-request")
router.register("customer-tasks", CustomerTaskViewSet, basename="customer-task")
router.register("partners", PartnerViewSet, basename="partner")

urlpatterns = router.urls
