from rest_framework.routers import DefaultRouter
from .views import (
    SupplierViewSet,
    ProductViewSet,
    SerializedUnitViewSet,
    StockReceiptViewSet,
    StockIssueViewSet,
)

router = DefaultRouter()
router.register("suppliers", SupplierViewSet, basename="supplier")
router.register("products", ProductViewSet, basename="product")
router.register("serialized-units", SerializedUnitViewSet, basename="serializedunit")
router.register("stock-receipts", StockReceiptViewSet, basename="stockreceipt")
router.register("stock-issues", StockIssueViewSet, basename="stockissue")

urlpatterns = router.urls
