import datetime
from decimal import Decimal

from accounts.models import User
from .models import AttendanceRecord, PayrollRunLine, PayrollSettings, StaffProfile, days_in_month


def salary_for_period(monthly_salary, period_start, period_end):
    """What a salaried employee is owed for a run's period.

    Month by month: each calendar month the period touches contributes
    `salary * days_of_it_in_the_period / days_in_that_month`. A whole
    calendar month therefore contributes exactly one salary, a part-month
    is prorated, and a period spanning several months adds up.

    That last case is why this exists. The previous version compared the
    period's length against the days in its FIRST month and paid a flat
    one month's salary whenever the period was at least that long:

        month_length = days_in_month(run.period_start)
        period_days = (period_end - period_start).days + 1
        if period_days >= month_length: base_pay = salary

    Nothing constrains a run to a single calendar month -- the only
    validation is period_end >= period_start -- so a two-month catch-up
    run (1 July to 31 August, 62 days against July's 31) paid one month's
    salary for two months worked. Every salaried employee underpaid by
    exactly one month, on a payslip PDF and a bank-export CSV that both
    printed the figure without complaint, and overtime came from
    attendance so the run still looked plausible.

    Rounded per month and then summed, rather than once at the end,
    because that is the arithmetic a payroll clerk checking a payslip
    would do by hand.
    """
    total = Decimal("0.00")
    cursor = period_start.replace(day=1)
    last_month = period_end.replace(day=1)
    while cursor <= last_month:
        in_month = days_in_month(cursor)
        month_end = cursor.replace(day=in_month)
        overlap_start = max(cursor, period_start)
        overlap_end = min(month_end, period_end)
        overlap_days = (overlap_end - overlap_start).days + 1
        if overlap_days >= in_month:
            total += monthly_salary
        else:
            total += (
                monthly_salary * Decimal(overlap_days) / Decimal(in_month)
            ).quantize(Decimal("0.01"))
        # First of the following month, without needing dateutil.
        cursor = month_end + datetime.timedelta(days=1)
    return total


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
            base_pay = salary_for_period(
                profile.monthly_salary or Decimal("0"), run.period_start, run.period_end
            )
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
