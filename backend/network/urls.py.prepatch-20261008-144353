from rest_framework.routers import DefaultRouter
from .views import (
    DeviceViewSet, NetworkSiteViewSet, IPPoolViewSet, IPAddressViewSet,
    ConnectionRuleViewSet, MonitoringReadingViewSet,
)

router = DefaultRouter()
router.register("devices", DeviceViewSet, basename="device")
router.register("network-sites", NetworkSiteViewSet, basename="networksite")
router.register("ip-pools", IPPoolViewSet, basename="ippool")
router.register("ip-addresses", IPAddressViewSet, basename="ipaddress")
router.register("connection-rules", ConnectionRuleViewSet, basename="connectionrule")
router.register("monitoring-readings", MonitoringReadingViewSet, basename="monitoringreading")

urlpatterns = router.urls
