from django.contrib import admin

from .models import AttendanceRecord, LeaveRequest, PayrollRun, PayrollRunLine, StaffProfile


@admin.register(StaffProfile)
class StaffProfileAdmin(admin.ModelAdmin):
    list_display = (
        "employee_number", "user", "pay_type", "monthly_salary", "hourly_rate",
        "annual_leave_balance", "sick_leave_balance", "family_responsibility_leave_balance", "is_active",
    )
    list_filter = ("pay_type", "is_active")


@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(admin.ModelAdmin):
    list_display = ("staff", "date", "clock_in", "clock_out", "worked_hours", "is_manual")
    list_filter = ("is_manual", "date")


class PayrollRunLineInline(admin.TabularInline):
    model = PayrollRunLine
    extra = 0


@admin.register(PayrollRun)
class PayrollRunAdmin(admin.ModelAdmin):
    list_display = ("period_start", "period_end", "status", "generated_by", "created_at")
    list_filter = ("status",)
    inlines = [PayrollRunLineInline]


@admin.register(LeaveRequest)
class LeaveRequestAdmin(admin.ModelAdmin):
    list_display = ("staff", "leave_type", "start_date", "end_date", "status", "decided_by")
    list_filter = ("leave_type", "status")
