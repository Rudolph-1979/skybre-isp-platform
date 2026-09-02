from rest_framework.routers import DefaultRouter

from django.urls import path

from .views import (
    AttendanceRecordViewSet, LeaveRequestViewSet, PayrollRunLineViewSet, PayrollRunViewSet,
    PayrollSettingsView, StaffProfileViewSet,
)

router = DefaultRouter()
router.register("staff-profiles", StaffProfileViewSet, basename="staffprofile")
router.register("attendance", AttendanceRecordViewSet, basename="attendance")
router.register("payroll-runs", PayrollRunViewSet, basename="payrollrun")
router.register("payroll-run-lines", PayrollRunLineViewSet, basename="payrollrunline")
router.register("leave-requests", LeaveRequestViewSet, basename="leaverequest")

urlpatterns = router.urls + [
    path("payroll-settings/", PayrollSettingsView.as_view(), name="payroll-settings"),
]
