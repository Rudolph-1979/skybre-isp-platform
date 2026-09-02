from decimal import Decimal

from accounts.models import User
from .models import AttendanceRecord, PayrollRunLine, PayrollSettings, StaffProfile, days_in_month


# Fields a person types in that this function must NOT destroy. Everything
# else on a line is derived from attendance and can be safely recomputed.
_MANUAL_LINE_FIELDS = (
    "paye",
    "additional_amount",
    "additional_description",
    "other_deduction_amount",
    "other_deduction_description",
    "notes",
)


def generate_payroll_run_lines(run):
    """Derives PayrollRunLine rows for `run` from attendance records that
    fall inside its period. Wipes and rebuilds `run`'s existing lines --
    callers (generate/recalculate) are responsible for only calling this
    on a draft run.

    Manually-entered figures survive the rebuild. PAYE in particular is typed
    in by hand (see PayrollRunLine.paye for why it isn't calculated), and a
    delete-and-rebuild would have thrown away every employee's tax figure the
    moment someone clicked Recalculate -- with nothing to say it had gone.
    They are re-applied by staff member, so they follow the right person even
    if the staff list changed between runs.
    """
    settings_obj = PayrollSettings.load()
    preserved = {
        line.staff_id: {field: getattr(line, field) for field in _MANUAL_LINE_FIELDS}
        for line in run.lines.all()
    }
    run.lines.all().delete()

    staff_qs = (
        User.objects.filter(role__in=["admin", "support", "sales", "technician", "management", "accounts"], is_active=True)
        .filter(staff_profile__is_active=True)
        .select_related("staff_profile")
    )

    lines = []
    for staff in staff_qs:
        profile = staff.staff_profile
        records = AttendanceRecord.objects.filter(
            staff=staff,
            date__gte=run.period_start,
            date__lte=run.period_end,
            clock_out__isnull=False,
        )
        regular_hours = Decimal("0.00")
        overtime_hours = Decimal("0.00")
        for record in records:
            regular_hours += record.regular_hours(profile.standard_daily_hours)
            overtime_hours += record.overtime_hours(profile.standard_daily_hours)

        rate = profile.hourly_equivalent_rate()
        overtime_rate = (rate * profile.overtime_multiplier).quantize(Decimal("0.01"))

        if profile.pay_type == StaffProfile.PayType.SALARY:
            salary = profile.monthly_salary or Decimal("0")
            month_length = days_in_month(run.period_start)
            period_days = (run.period_end - run.period_start).days + 1
            if period_days >= month_length:
                base_pay = salary
            else:
                base_pay = (salary * Decimal(period_days) / Decimal(month_length)).quantize(Decimal("0.01"))
        else:
            base_pay = (rate * regular_hours).quantize(Decimal("0.01"))

        overtime_pay = (overtime_rate * overtime_hours).quantize(Decimal("0.01"))
        gross_pay = base_pay + overtime_pay

        carried = preserved.get(staff.id, {})
        # UIF and SDL are recomputed rather than carried, because they are a
        # function of remuneration -- if the hours changed, so must they.
        # Remuneration includes any additional amount (a bonus is remuneration
        # for UIF purposes), which is why this happens after the carry-over.
        remuneration = gross_pay + carried.get("additional_amount", Decimal("0"))
        uif_employee, uif_employer = settings_obj.uif_for(remuneration)
        sdl = settings_obj.sdl_for(remuneration)

        lines.append(
            PayrollRunLine(
                payroll_run=run,
                staff=staff,
                pay_type=profile.pay_type,
                regular_hours=regular_hours,
                overtime_hours=overtime_hours,
                hourly_rate=rate,
                overtime_rate=overtime_rate,
                base_pay=base_pay,
                overtime_pay=overtime_pay,
                gross_pay=gross_pay,
                uif_employee=uif_employee,
                uif_employer=uif_employer,
                sdl=sdl,
                **carried,
            )
        )

    PayrollRunLine.objects.bulk_create(lines)
    return run


def recalculate_statutory(line):
    """Recompute UIF and SDL for one line and save them.

    Needed because those depend on remuneration, and remuneration changes when
    someone edits the additional amount -- so editing a bonus has to move the
    UIF with it or the payslip is wrong. Called from the line serializer's
    update rather than a signal, so a bulk regenerate isn't doing it twice.
    """
    settings_obj = PayrollSettings.load()
    remuneration = line.gross_pay + line.additional_amount
    line.uif_employee, line.uif_employer = settings_obj.uif_for(remuneration)
    line.sdl = settings_obj.sdl_for(remuneration)
    line.save(update_fields=["uif_employee", "uif_employer", "sdl"])
    return line
