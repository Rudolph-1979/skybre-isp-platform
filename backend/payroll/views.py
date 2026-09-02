import csv
from decimal import Decimal

from django.http import HttpResponse
from django.utils import timezone
from rest_framework import mixins, permissions, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsAdmin, IsStaffMember
from .filters import AttendanceRecordFilter, LeaveRequestFilter
from .models import AttendanceRecord, LeaveRequest, PayrollRun, PayrollRunLine, PayrollSettings, StaffProfile
from .serializers import (
    AttendanceRecordSerializer,
    LeaveRequestSerializer,
    PayrollRunLineSerializer,
    PayrollRunSerializer,
    PayrollSettingsSerializer,
    StaffProfileSerializer,
)
from .services import generate_payroll_run_lines


class StaffProfileViewSet(viewsets.ModelViewSet):
    serializer_class = StaffProfileSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    queryset = StaffProfile.objects.select_related("user").all()
    filterset_fields = ["is_active", "pay_type", "user"]

    @action(detail=False, methods=["get"], permission_classes=[permissions.IsAuthenticated])
    def me(self, request):
        """Any authenticated staff member can read their own profile (to
        show pay type on their own attendance view) -- everything else on
        this viewset is admin-only."""
        profile = getattr(request.user, "staff_profile", None)
        return Response(StaffProfileSerializer(profile).data if profile else None)


class AttendanceRecordViewSet(viewsets.ModelViewSet):
    serializer_class = AttendanceRecordSerializer
    permission_classes = [permissions.IsAuthenticated, IsStaffMember]
    filterset_class = AttendanceRecordFilter

    def get_queryset(self):
        qs = AttendanceRecord.objects.select_related("staff", "staff__staff_profile").all()
        user = self.request.user
        if user.role != user.Role.ADMIN:
            qs = qs.filter(staff=user)
        return qs

    def perform_create(self, serializer):
        # Self clock-in/out goes through the dedicated actions below. This
        # generic create path is for admins backfilling/correcting records.
        if self.request.user.role != self.request.user.Role.ADMIN:
            raise PermissionDenied("Only an admin can add attendance records directly. Use clock in/out instead.")
        serializer.save(is_manual=True)

    def perform_update(self, serializer):
        if self.request.user.role != self.request.user.Role.ADMIN:
            raise PermissionDenied("Only an admin can edit attendance records.")
        serializer.save(is_manual=True)

    def perform_destroy(self, instance):
        if self.request.user.role != self.request.user.Role.ADMIN:
            raise PermissionDenied("Only an admin can delete attendance records.")
        instance.delete()

    @action(detail=False, methods=["get"])
    def open(self, request):
        """The requesting user's currently open (no clock-out yet) record,
        if any -- drives the Clock in/out button's state."""
        record = (
            AttendanceRecord.objects.filter(staff=request.user, clock_out__isnull=True)
            .order_by("-clock_in")
            .first()
        )
        return Response(AttendanceRecordSerializer(record).data if record else None)

    @action(detail=False, methods=["post"])
    def clock_in(self, request):
        if AttendanceRecord.objects.filter(staff=request.user, clock_out__isnull=True).exists():
            return Response({"detail": "You're already clocked in."}, status=400)
        now = timezone.localtime(timezone.now())
        record = AttendanceRecord.objects.create(staff=request.user, date=now.date(), clock_in=now)
        return Response(AttendanceRecordSerializer(record).data, status=201)

    @action(detail=False, methods=["post"])
    def clock_out(self, request):
        record = (
            AttendanceRecord.objects.filter(staff=request.user, clock_out__isnull=True)
            .order_by("-clock_in")
            .first()
        )
        if not record:
            return Response({"detail": "You're not currently clocked in."}, status=400)
        record.clock_out = timezone.now()
        note = request.data.get("notes")
        if note:
            record.notes = note
        record.save(update_fields=["clock_out", "notes"])
        return Response(AttendanceRecordSerializer(record).data)


class PayrollRunViewSet(viewsets.ModelViewSet):
    serializer_class = PayrollRunSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    queryset = PayrollRun.objects.prefetch_related("lines__staff__staff_profile").all()

    def perform_create(self, serializer):
        run = serializer.save(generated_by=self.request.user)
        generate_payroll_run_lines(run)

    def perform_destroy(self, instance):
        # A finalized run is the record of what was actually paid --
        # deletable draft runs are just abandoned/incorrect calculations,
        # but a finalized one should be corrected by a new run, not erased.
        if instance.status == PayrollRun.Status.FINALIZED:
            raise PermissionDenied("A finalized payroll run can't be deleted.")
        instance.delete()

    @action(detail=True, methods=["post"])
    def recalculate(self, request, pk=None):
        """Re-derives this run's lines from current attendance records --
        useful if attendance was corrected after a draft run was
        generated. Blocked once finalized so a signed-off run can't
        silently change under someone."""
        run = self.get_object()
        if run.status == PayrollRun.Status.FINALIZED:
            return Response({"detail": "Cannot recalculate a finalized payroll run."}, status=400)
        generate_payroll_run_lines(run)
        run.refresh_from_db()
        return Response(PayrollRunSerializer(run).data)

    @action(detail=True, methods=["post"])
    def finalize(self, request, pk=None):
        run = self.get_object()
        if run.status == PayrollRun.Status.FINALIZED:
            return Response({"detail": "This payroll run is already finalized."}, status=400)
        run.status = PayrollRun.Status.FINALIZED
        run.finalized_at = timezone.now()
        run.save(update_fields=["status", "finalized_at"])
        return Response(PayrollRunSerializer(run).data)

    @action(detail=True, methods=["get"])
    def export_csv(self, request, pk=None):
        run = self.get_object()
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = (
            f'attachment; filename="payroll_{run.period_start}_{run.period_end}.csv"'
        )
        writer = csv.writer(response)
        writer.writerow([
            "Employee number", "Name", "Pay type", "Regular hours", "Overtime hours",
            "Hourly rate", "Overtime rate", "Base pay", "Overtime pay", "Gross pay",
            "Additional", "Additional description", "Total earnings",
            "PAYE", "UIF (employee)", "Other deduction", "Other deduction description",
            "Total deductions", "Net pay",
            "UIF (employer)", "SDL", "Cost to company",
        ])
        for line in run.lines.select_related("staff", "staff__staff_profile").all():
            profile = getattr(line.staff, "staff_profile", None)
            writer.writerow([
                profile.employee_number if profile else "",
                line.staff.get_full_name() or line.staff.username,
                line.get_pay_type_display(),
                line.regular_hours,
                line.overtime_hours,
                line.hourly_rate,
                line.overtime_rate,
                line.base_pay,
                line.overtime_pay,
                line.gross_pay,
                line.additional_amount,
                line.additional_description,
                line.total_earnings,
                line.paye,
                line.uif_employee,
                line.other_deduction_amount,
                line.other_deduction_description,
                line.total_deductions,
                line.net_pay,
                line.uif_employer,
                line.sdl,
                line.cost_to_company,
            ])
        return response

    @action(detail=True, methods=["get"], url_path="payslips-pdf")
    def payslips_pdf(self, request, pk=None):
        """Every payslip in this run as one PDF, page-broken per employee.

        Same renderer as the single payslip below, so the two cannot disagree
        about anyone's net pay.
        """
        from notifications.pdf import render_payslip_pdf

        run = self.get_object()
        lines = list(run.lines.select_related("staff", "staff__staff_profile").all())
        if not lines:
            return Response({"detail": "This run has no lines to print."}, status=400)
        pdf_bytes = render_payslip_pdf(lines)
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = (
            f'inline; filename="payslips_{run.period_start}_{run.period_end}.pdf"'
        )
        return response


class PayrollRunLineViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """Individual payroll lines -- list, read, and update the figures a person
    enters (PAYE, an extra amount, another deduction, a note).

    No create and no delete: lines exist because a payroll run generated them
    from attendance, and one appearing or vanishing on its own would make the
    run disagree with the attendance it was derived from.

    Editing is blocked once the run is finalised. That is the whole point of
    finalising -- a signed-off run is the record of what was actually paid, and
    a correction belongs in a new run rather than rewritten history.
    """

    serializer_class = PayrollRunLineSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    filterset_fields = ["payroll_run", "staff"]

    def get_queryset(self):
        return PayrollRunLine.objects.select_related(
            "staff", "staff__staff_profile", "payroll_run"
        ).all()

    def perform_update(self, serializer):
        if serializer.instance.payroll_run.status == PayrollRun.Status.FINALIZED:
            raise PermissionDenied(
                "This payroll run is finalised. Correct it with a new run rather than editing this one."
            )
        serializer.save()

    @action(detail=True, methods=["get"], url_path="payslip-pdf")
    def payslip_pdf(self, request, pk=None):
        """One employee's payslip."""
        from notifications.pdf import render_payslip_pdf

        line = self.get_object()
        pdf_bytes = render_payslip_pdf([line])
        name = (line.staff.get_full_name() or line.staff.username).replace(" ", "-")
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = (
            f'inline; filename="payslip-{name}-{line.payroll_run.period_end}.pdf"'
        )
        return response

    @action(detail=True, methods=["post"], url_path="email-payslip")
    def email_payslip(self, request, pk=None):
        """Email this payslip to the employee.

        Sent through notifications.services so it goes out on the same SMTP
        settings as everything else and lands in the same email log -- a
        payslip is exactly the kind of thing you need to be able to prove you
        sent. Refused on a draft run: a figure that can still change should not
        be in someone's inbox looking final.
        """
        from notifications.pdf import render_payslip_pdf
        from notifications.services import send_payslip_email

        line = self.get_object()
        if line.payroll_run.status != PayrollRun.Status.FINALIZED:
            return Response(
                {"detail": "Finalise the payroll run before emailing payslips — draft figures can still change."},
                status=400,
            )
        recipient = (line.staff.email or "").strip()
        if not recipient:
            return Response(
                {"detail": f"No email address on file for {line.staff.get_full_name() or line.staff.username}."},
                status=400,
            )
        log = send_payslip_email(line, render_payslip_pdf([line]), sent_by_id=request.user.id)
        return Response(
            {"detail": f"Payslip sent to {recipient}." if log.status == "sent" else log.error_message},
            status=200 if log.status == "sent" else 502,
        )


class LeaveRequestViewSet(viewsets.ModelViewSet):
    serializer_class = LeaveRequestSerializer
    permission_classes = [permissions.IsAuthenticated, IsStaffMember]
    filterset_class = LeaveRequestFilter

    def get_queryset(self):
        qs = LeaveRequest.objects.select_related("staff", "staff__staff_profile", "decided_by").all()
        user = self.request.user
        if user.role != user.Role.ADMIN:
            qs = qs.filter(staff=user)
        return qs

    def perform_create(self, serializer):
        # Self-service: anyone can request their own leave. An admin may
        # instead specify any staff member, e.g. backfilling a request
        # that came in on paper or by phone.
        if self.request.user.role != self.request.user.Role.ADMIN:
            serializer.save(staff=self.request.user)
        else:
            serializer.save()

    def perform_update(self, serializer):
        instance = serializer.instance
        user = self.request.user
        if instance.status != LeaveRequest.Status.PENDING and user.role != user.Role.ADMIN:
            raise PermissionDenied("This leave request has already been decided and can no longer be edited.")
        serializer.save()

    def perform_destroy(self, instance):
        user = self.request.user
        if instance.status != LeaveRequest.Status.PENDING and user.role != user.Role.ADMIN:
            raise PermissionDenied("This leave request has already been decided and can no longer be withdrawn.")
        instance.delete()

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        if request.user.role != request.user.Role.ADMIN:
            raise PermissionDenied("Only an admin can approve leave requests.")
        leave = self.get_object()
        if leave.status != LeaveRequest.Status.PENDING:
            return Response({"detail": "This request has already been decided."}, status=400)

        profile = getattr(leave.staff, "staff_profile", None)
        if profile is not None:
            field_name = leave.balance_field_name()
            current_balance = getattr(profile, field_name)
            days = Decimal(leave.days_requested)
            if days > current_balance:
                return Response(
                    {
                        "detail": (
                            f"{leave.staff.get_full_name() or leave.staff.username} only has "
                            f"{current_balance} days of {leave.get_leave_type_display().lower()} left, "
                            f"but this request is for {days} days. Adjust their balance under Staff → "
                            "Employees first if this should still be approved."
                        )
                    },
                    status=400,
                )
            setattr(profile, field_name, current_balance - days)
            profile.save(update_fields=[field_name])

        leave.status = LeaveRequest.Status.APPROVED
        leave.decided_by = request.user
        leave.decided_at = timezone.now()
        leave.save(update_fields=["status", "decided_by", "decided_at"])
        return Response(LeaveRequestSerializer(leave, context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        if request.user.role != request.user.Role.ADMIN:
            raise PermissionDenied("Only an admin can reject leave requests.")
        leave = self.get_object()
        if leave.status != LeaveRequest.Status.PENDING:
            return Response({"detail": "This request has already been decided."}, status=400)
        leave.status = LeaveRequest.Status.REJECTED
        leave.decision_note = request.data.get("decision_note", "")
        leave.decided_by = request.user
        leave.decided_at = timezone.now()
        leave.save(update_fields=["status", "decision_note", "decided_by", "decided_at"])
        return Response(LeaveRequestSerializer(leave, context={"request": request}).data)


class PayrollSettingsView(APIView):
    """The statutory rates and employer details a payslip needs.

    GET/PATCH on the singleton, same convention as BillingDefaultsView and
    EmailSettingsView. Admin-only: these numbers decide what is withheld from
    everybody's pay.
    """

    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def get(self, request):
        return Response(PayrollSettingsSerializer(PayrollSettings.load()).data)

    def patch(self, request):
        serializer = PayrollSettingsSerializer(PayrollSettings.load(), data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)
