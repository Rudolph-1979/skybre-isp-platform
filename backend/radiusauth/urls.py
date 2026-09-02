from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import (
    RadiusNasClientViewSet, RadAcctViewSet, OvpnSettingsView, OvpnClientConnectionViewSet,
    CustomerUsageView, CustomerLiveRateView, PublicUsageView, UsageReportView,
    OfflineCustomersView, SpeedWindowViewSet, ServiceSpeedNowView,
)

router = DefaultRouter()
router.register("radius-nas-clients", RadiusNasClientViewSet, basename="radiusnasclient")
router.register("radius-sessions", RadAcctViewSet, basename="radacct")
router.register("ovpn-client-connections", OvpnClientConnectionViewSet, basename="ovpnclientconnection")
router.register("speed-windows", SpeedWindowViewSet, basename="speedwindow")

urlpatterns = router.urls + [
    path("ovpn-settings/", OvpnSettingsView.as_view(), name="ovpn-settings"),
    path("customers/<int:pk>/usage/", CustomerUsageView.as_view(), name="customer-usage"),
    path("customers/<int:pk>/live/", CustomerLiveRateView.as_view(), name="customer-live-rate"),
    # Deliberately unauthenticated -- the token in the path IS the
    # credential. See PublicUsageView for what that means for what it
    # returns.
    path("usage-report/", UsageReportView.as_view(), name="usage-report"),
    path("offline-customers/", OfflineCustomersView.as_view(), name="offline-customers"),
    path("services/<int:pk>/speed-now/", ServiceSpeedNowView.as_view(), name="service-speed-now"),
    path("public/usage/<str:token>/", PublicUsageView.as_view(), name="public-usage"),
]
