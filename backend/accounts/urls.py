from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView
from .views import (
    CustomTokenObtainPairView,
    MeView,
    DashboardSummaryView,
    StaffListView,
    TwoFactorStatusView,
    TwoFactorSetupView,
    TwoFactorConfirmView,
    TwoFactorDisableView,
)

urlpatterns = [
    path("token/", CustomTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("me/", MeView.as_view(), name="me"),
    path("dashboard-summary/", DashboardSummaryView.as_view(), name="dashboard_summary"),
    path("staff-users/", StaffListView.as_view(), name="staff_users"),
    path("2fa/status/", TwoFactorStatusView.as_view(), name="2fa_status"),
    path("2fa/setup/", TwoFactorSetupView.as_view(), name="2fa_setup"),
    path("2fa/confirm/", TwoFactorConfirmView.as_view(), name="2fa_confirm"),
    path("2fa/disable/", TwoFactorDisableView.as_view(), name="2fa_disable"),
]
