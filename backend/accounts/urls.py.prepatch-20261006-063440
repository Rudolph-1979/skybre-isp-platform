from django.urls import path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView
from .views import (
    CustomTokenObtainPairView,
    MeView,
    DashboardSummaryView,
    CustomerGrowthView,
    HighAlertCustomersView,
    StaffListView,
    StaffPermissionsViewSet,
    StaffAccountsViewSet,
    TwoFactorStatusView,
    TwoFactorSetupView,
    TwoFactorConfirmView,
    TwoFactorDisableView,
    PasswordResetRequestView,
    PasswordResetConfirmView,
)

router = DefaultRouter()
router.register("staff-permissions", StaffPermissionsViewSet, basename="staffpermissions")
router.register("staff-accounts", StaffAccountsViewSet, basename="staffaccounts")

urlpatterns = [
    path("token/", CustomTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("me/", MeView.as_view(), name="me"),
    path("dashboard-summary/", DashboardSummaryView.as_view(), name="dashboard_summary"),
    path("customer-growth/", CustomerGrowthView.as_view(), name="customer_growth"),
    path("high-alert-customers/", HighAlertCustomersView.as_view(), name="high_alert_customers"),
    path("staff-users/", StaffListView.as_view(), name="staff_users"),
    path("2fa/status/", TwoFactorStatusView.as_view(), name="2fa_status"),
    path("2fa/setup/", TwoFactorSetupView.as_view(), name="2fa_setup"),
    path("2fa/confirm/", TwoFactorConfirmView.as_view(), name="2fa_confirm"),
    path("2fa/disable/", TwoFactorDisableView.as_view(), name="2fa_disable"),
    path("password-reset/", PasswordResetRequestView.as_view(), name="password_reset_request"),
    path("password-reset/confirm/", PasswordResetConfirmView.as_view(), name="password_reset_confirm"),
    *router.urls,
]
