from rest_framework.routers import DefaultRouter
from .views import TariffViewSet, ServiceViewSet, InvoiceViewSet, PaymentViewSet

router = DefaultRouter()
router.register("tariffs", TariffViewSet, basename="tariff")
router.register("services", ServiceViewSet, basename="service")
router.register("invoices", InvoiceViewSet, basename="invoice")
router.register("payments", PaymentViewSet, basename="payment")

urlpatterns = router.urls
