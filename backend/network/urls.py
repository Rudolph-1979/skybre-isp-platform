from rest_framework.routers import DefaultRouter
from .views import DeviceViewSet, IPPoolViewSet, IPAddressViewSet, MonitoringReadingViewSet

router = DefaultRouter()
router.register("devices", DeviceViewSet, basename="device")
router.register("ip-pools", IPPoolViewSet, basename="ippool")
router.register("ip-addresses", IPAddressViewSet, basename="ipaddress")
router.register("monitoring-readings", MonitoringReadingViewSet, basename="monitoringreading")

urlpatterns = router.urls
