from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import LeadViewSet, PipelineSummaryView

router = DefaultRouter()
router.register("leads", LeadViewSet, basename="lead")

urlpatterns = [
    path("pipeline-summary/", PipelineSummaryView.as_view(), name="pipeline_summary"),
    *router.urls,
]
