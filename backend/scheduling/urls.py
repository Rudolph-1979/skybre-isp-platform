from rest_framework.routers import DefaultRouter
from .views import JobViewSet, ShiftViewSet

router = DefaultRouter()
router.register("jobs", JobViewSet, basename="job")
router.register("shifts", ShiftViewSet, basename="shift")

urlpatterns = router.urls
