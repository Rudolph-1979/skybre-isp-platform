from rest_framework.routers import DefaultRouter

from .views import FuelLogViewSet, OdometerReadingViewSet, ServiceRecordViewSet, VehicleViewSet

router = DefaultRouter()
router.register("vehicles", VehicleViewSet, basename="vehicle")
router.register("odometer-readings", OdometerReadingViewSet, basename="odometerreading")
router.register("service-records", ServiceRecordViewSet, basename="servicerecord")
router.register("fuel-logs", FuelLogViewSet, basename="fuellog")

urlpatterns = router.urls
