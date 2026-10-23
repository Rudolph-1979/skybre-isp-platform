from rest_framework.routers import DefaultRouter
from django.urls import path
from .views import JobTypeViewSet, JobViewSet, SchedulingSettingsView, ShiftViewSet
from .rotation import SupportRotationViewSet

router = DefaultRouter()
router.register("jobs", JobViewSet, basename="job")
router.register("shifts", ShiftViewSet, basename="shift")
router.register("job-types", JobTypeViewSet, basename="job-type")
# Weekly support rotations (#184, 16 Oct 2026).
router.register("support-rotations", SupportRotationViewSet, basename="support-rotation")

urlpatterns = router.urls + [
    path("scheduling-settings/", SchedulingSettingsView.as_view(), name="scheduling-settings"),
]
