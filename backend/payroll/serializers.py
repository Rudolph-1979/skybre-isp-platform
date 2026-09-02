from decimal import Decimal

from django.conf import settings
from rest_framework import serializers

from config.media_security import sign_media_path
from .models import (
    AttendanceRecord, LeaveRequest, PayrollRun, PayrollRunLine, PayrollSettings, StaffProfile,
)
from .services import recalculate_statutory


class StaffProfileSerializer(serializers.ModelSerializer):
    staff_name = serializers.SerializerMethodField()
    username = serializers.CharField(source="user.username", read_only=True)
    role = serializers.CharField(source="user.role", read_only=True)
    phone = serializers.CharField(source="user.phone", read_only=True)

    class Meta:
        model = StaffProfile
        fields = [
            "id", "user", "staff_name", "username", "role", "phone", "employee_number",
            "id_number", "license_number",
            "pay_type", "monthly_salary", "hourly_rate", "standard_daily_hours",
            "overtime_multiplier",
            "annual_leave_balance", "sick_leave_balance", "family_responsibility_leave_balance",
            "is_active", "created_at",
        ]
        read_only_fields = ["id", "employee_number", "created_at"]

    def get_staff_name(self, obj):
        return obj.user.get_full_name() or obj.user.username

    def validate(self, attrs):
        pay_type = attrs.get("pay_type", getattr(self.instance, "pay_type", None))
        monthly_salary = attrs.get("monthly_salary", getattr(self.instance, "monthly_salary", None))
        hourly_rate = attrs.get("hourly_rate", getattr(self.instance, "hourly_rate", None))
        if pay_type == StaffProfile.PayType.SALARY and not monthly_salary:
            raise serializers.ValidationError("Monthly salary is required for salaried staff.")
        if pay_type == StaffProfile.PayType.HOURLY and not hourly_rate:
            raise serializers.ValidationError("Hourly rate is required for hourly-paid staff.")
        return attrs


class AttendanceRecordSerializer(serializers.ModelSerializer):
    staff_name = serializers.SerializerMethodField()
    worked_hours = serializers.SerializerMethodField()
    overtime_hours = serializers.SerializerMethodField()

    class Meta:
        model = AttendanceRecord
        fields = [
            "id", "staff", "staff_name", "date", "clock_in", "clock_out",
            "notes", "is_manual", "worked_hours", "overtime_hours", "created_at",
        ]
        read_only_fields = ["id", "is_manual", "created_at"]

    def get_staff_name(self, obj):
        return obj.staff.get_full_name() or obj.staff.username

    def _threshold(self, obj):
        profile = getattr(obj.staff, "staff_profile", None)
        return profile.standard_daily_hours if profile else Decimal("8.00")

    def get_worked_hours(self, obj):
        return str(obj.worked_hours)

    def get_overtime_hours(self, obj):
        return str(obj.overtime_hours(self._threshold(obj)))

    def validate(self, attrs):
        clock_in = attrs.get("clock_in", getattr(self.instance, "clock_in", None))
        clock_out = attrs.get("clock_out", getattr(self.instance, "clock_out", None))
        if clock_out and clock_in and clock_out <= clock_in:
            raise serializers.ValidationError("Clock out must be after clock in.")
        return attrs


class PayrollRunLineSerializer(serializers.ModelSerializer):
    """The hours and rates are read-only -- they are derived from attendance
    and recomputed by services.generate_payroll_run_lines. Writable here is
    only what a person legitimately types: PAYE, an extra amount, another
    deduction, a note.

    Editing the extra amount moves UIF with it (a bonus is remuneration for
    UIF purposes), so update() recomputes the statutory figures rather than
    leaving them stale against a changed gross.
    """

    staff_name = serializers.SerializerMethodField()
    employee_number = serializers.SerializerMethodField()
    id_number = serializers.SerializerMethodField()
    total_earnings = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    total_deductions = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    net_pay = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    employer_contributions = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    cost_to_company = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = PayrollRunLine
        fields = [
            "id", "staff", "staff_name", "employee_number", "id_number", "pay_type",
            "regular_hours", "overtime_hours", "hourly_rate", "overtime_rate",
            "base_pay", "overtime_pay", "gross_pay",
            "additional_amount", "additional_description",
            "paye", "uif_employee", "other_deduction_amount", "other_deduction_description",
            "uif_employer", "sdl", "notes",
            "total_earnings", "total_deductions", "net_pay",
            "employer_contributions", "cost_to_company",
        ]
        read_only_fields = [
            "id", "staff", "pay_type",
            "regular_hours", "overtime_hours", "hourly_rate", "overtime_rate",
            "base_pay", "overtime_pay", "gross_pay",
            # Calculated from remuneration, never typed -- see PayrollSettings.
            "uif_employee", "uif_employer", "sdl",
        ]

    def get_staff_name(self, obj):
        return obj.staff.get_full_name() or obj.staff.username

    def get_employee_number(self, obj):
        profile = getattr(obj.staff, "staff_profile", None)
        return profile.employee_number if profile else ""

    def get_id_number(self, obj):
        profile = getattr(obj.staff, "staff_profile", None)
        return profile.id_number if profile else ""

    def update(self, instance, validated_data):
        line = super().update(instance, validated_data)
        return recalculate_statutory(line)


class PayrollSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = PayrollSettings
        fields = [
            "uif_rate_pct", "uif_monthly_ceiling", "sdl_rate_pct", "sdl_applicable",
            "employer_name", "employer_address", "paye_reference", "uif_reference",
            "payslip_note", "updated_at",
        ]
        read_only_fields = ["updated_at"]


class PayrollRunSerializer(serializers.ModelSerializer):
    lines = PayrollRunLineSerializer(many=True, read_only=True)
    generated_by_name = serializers.CharField(source="generated_by.username", read_only=True, default=None)
    total_regular_hours = serializers.SerializerMethodField()
    total_overtime_hours = serializers.SerializerMethodField()
    total_gross_pay = serializers.SerializerMethodField()
    total_paye = serializers.SerializerMethodField()
    total_uif_employee = serializers.SerializerMethodField()
    total_deductions = serializers.SerializerMethodField()
    total_net_pay = serializers.SerializerMethodField()
    total_cost_to_company = serializers.SerializerMethodField()
    staff_count = serializers.SerializerMethodField()

    class Meta:
        model = PayrollRun
        fields = [
            "id", "period_start", "period_end", "status", "generated_by", "generated_by_name",
            "created_at", "finalized_at", "lines",
            "total_regular_hours", "total_overtime_hours", "total_gross_pay",
            "total_paye", "total_uif_employee", "total_deductions", "total_net_pay",
            "total_cost_to_company", "staff_count",
        ]
        read_only_fields = ["id", "status", "generated_by", "created_at", "finalized_at", "lines"]

    def validate(self, attrs):
        if attrs["period_end"] < attrs["period_start"]:
            raise serializers.ValidationError("Period end must be on or after period start.")
        return attrs

    def get_total_paye(self, obj):
        return self._sum(obj, "paye")

    def get_total_uif_employee(self, obj):
        return self._sum(obj, "uif_employee")

    def get_total_deductions(self, obj):
        return sum((line.total_deductions for line in obj.lines.all()), Decimal("0"))

    def get_total_net_pay(self, obj):
        return sum((line.net_pay for line in obj.lines.all()), Decimal("0"))

    def get_total_cost_to_company(self, obj):
        return sum((line.cost_to_company for line in obj.lines.all()), Decimal("0"))

    def _sum(self, obj, field):
        total = Decimal("0")
        for line in obj.lines.all():
            total += getattr(line, field)
        return str(total)

    def get_total_regular_hours(self, obj):
        return self._sum(obj, "regular_hours")

    def get_total_overtime_hours(self, obj):
        return self._sum(obj, "overtime_hours")

    def get_total_gross_pay(self, obj):
        return self._sum(obj, "gross_pay")

    def get_staff_count(self, obj):
        return obj.lines.count()


class LeaveRequestSerializer(serializers.ModelSerializer):
    staff_name = serializers.SerializerMethodField()
    employee_number = serializers.SerializerMethodField()
    days_requested = serializers.IntegerField(read_only=True)
    decided_by_name = serializers.CharField(source="decided_by.username", read_only=True, default=None)

    class Meta:
        model = LeaveRequest
        fields = [
            "id", "staff", "staff_name", "employee_number", "leave_type", "start_date", "end_date",
            "days_requested", "reason", "attachment", "status", "decision_note",
            "decided_by", "decided_by_name", "decided_at", "created_at",
        ]
        read_only_fields = ["id", "status", "decision_note", "decided_by", "decided_at", "created_at"]

    def get_staff_name(self, obj):
        return obj.staff.get_full_name() or obj.staff.username

    def get_employee_number(self, obj):
        profile = getattr(obj.staff, "staff_profile", None)
        return profile.employee_number if profile else ""

    def to_representation(self, instance):
        # Same signed-link approach as inventory's supplier-invoice
        # attachments -- a sick note is staff medical information, so it
        # shouldn't be reachable via a bare, unauthenticated /media/ URL.
        data = super().to_representation(instance)
        if instance.attachment:
            relative_path = instance.attachment.name
            signed = sign_media_path(relative_path)
            url = f"{settings.MEDIA_URL}{relative_path}?sig={signed}"
            request = self.context.get("request")
            data["attachment"] = request.build_absolute_uri(url) if request else url
        else:
            data["attachment"] = None
        return data

    def validate(self, attrs):
        start = attrs.get("start_date", getattr(self.instance, "start_date", None))
        end = attrs.get("end_date", getattr(self.instance, "end_date", None))
        if start and end and end < start:
            raise serializers.ValidationError("End date must be on or after start date.")
        return attrs
