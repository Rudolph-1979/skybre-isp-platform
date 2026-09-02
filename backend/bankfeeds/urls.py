from rest_framework.routers import DefaultRouter

from .views import BankAccountViewSet, BankTransactionViewSet, BankFeedSyncLogViewSet

router = DefaultRouter()
router.register("bank-accounts", BankAccountViewSet, basename="bankaccount")
router.register("bank-transactions", BankTransactionViewSet, basename="banktransaction")
router.register("bank-feed-sync-logs", BankFeedSyncLogViewSet, basename="bankfeedsynclog")

urlpatterns = router.urls
