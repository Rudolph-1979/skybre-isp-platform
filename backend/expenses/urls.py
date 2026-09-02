from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import ExpenseViewSet, VatReturnView, VatReturnPdfView

router = DefaultRouter()
router.register("expenses", ExpenseViewSet, basename="expense")

urlpatterns = router.urls + [
    path("vat-return/", VatReturnView.as_view(), name="vat-return"),
    path("vat-return/pdf/", VatReturnPdfView.as_view(), name="vat-return-pdf"),
]
