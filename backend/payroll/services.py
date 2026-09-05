import datetime
from decimal import Decimal

from django.db.models import Q

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


def uif_for_period(settings_obj, remuneration, period_start, period_end):
    """(employee, employer) UIF for a run's period, capped per MONTH.

    PayrollSettings.uif_for caps at one month's ceiling, which is the rule:
    1% from the employee and 1% matched by the employer, on remuneration up
    to the ceiling FOR A MONTH. Nothing constrains a run to one calendar
    month, though -- the only validation is period_end >= period_start -- so
    calling it once with a whole period's remuneration squeezed two months of
    pay under a single month's cap. A two-month catch-up run for anyone
    earning over the ceiling took R177.12 off the employee instead of
    R354.24, and declared the same shortfall in the employer's own
    contribution on the EMP201 -- which is the employer's to make good later,
    with penalties and interest.

    Remuneration is split across the months by each month's share of the
    period's days, then each month is capped and rounded on its own -- the
    same month-by-month-then-sum arithmetic as salary_for_period, and what a
    clerk writing one payslip per month by hand would arrive at. A part month
    still gets a whole month's ceiling: the ceiling limits what a month's
    earnings are charged on, it does not accrue by the day.

    A period inside one calendar month gives a share equal to the whole
    remuneration, so the ordinary monthly run is unchanged to the cent.
    """
    period_days = (period_end - period_start).days + 1
    employee = Decimal("0.00")
    employer = Decimal("0.00")
    cursor = period_start.replace(day=1)
    last_month = period_end.replace(day=1)
    while cursor <= last_month:
        month_end = cursor.replace(day=days_in_month(cursor))
        overlap_start = max(cursor, period_start)
        overlap_end = min(month_end, period_end)
        overlap_days = (overlap_end - overlap_start).days + 1
        share = (
            remuneration * Decimal(overlap_days) / Decimal(period_days)
        ).quantize(Decimal("0.01"))
        month_employee, month_employer = settings_obj.uif_for(share)
        employee += month_employee
        employer += month_employer
        cursor = month_end + datetime.timedelta(days=1)
    return employee, employer


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

    # Who gets a line: everyone currently on the payroll, plus anyone who
    # already had one on this run. That second half is the leaver. Someone
    # deactivated between a draft being generated and it being recalculated
    # has almost always just left, and that line is their final pay -- but
    # rebuilding from the active staff alone dropped it, along with the PAYE
    # somebody had typed against it, and the run came back one person shorter
    # with nothing said about it. The page redraws itself from the response,
    # and the bank CSV and the payslip PDFs are all driven off these lines, so
    # the first sign of it was someone not being paid. Recalculate must never
    # be the thing that quietly decides not to pay somebody.
    #
    # It still won't ADD anyone who left before the period: they have no line
    # on this run, so they aren't in `preserved`, so they aren't matched here.
    # The staff_profile guard is what the old is_active filter gave us for
    # free -- the loop below reads staff.staff_profile, and a line can outlive
    # the profile it was generated from.
    staff_qs = (
        User.objects.filter(staff_profile__isnull=False)
        .filter(
            Q(
                role__in=["admin", "support", "sales", "technician", "management", "accounts"],
                is_active=True,
                staff_profile__is_active=True,
            )
            | Q(pk__in=list(preserved))
        )
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
        # Per month, not per run: uif_for caps at one month's ceiling, so a
        # two-month catch-up run used to charge UIF on one month of it while
        # paying two months' salary. SDL has no ceiling, so 1% of the whole
        # period is right as it stands.
        uif_employee, uif_employer = uif_for_period(
            settings_obj, remuneration, run.period_start, run.period_end
        )
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
    # Capped per month, exactly as generation does it -- otherwise typing a
    # bonus into a line on a multi-month run quietly put the single-month cap
    # back on a run that had been generated correctly.
    run = line.payroll_run
    line.uif_employee, line.uif_employer = uif_for_period(
        settings_obj, remuneration, run.period_start, run.period_end
    )
    line.sdl = settings_obj.sdl_for(remuneration)
    line.save(update_fields=["uif_employee", "uif_employer", "sdl"])
    return line
