import calendar
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import models

from config.uploads import ATTACHMENT_VALIDATORS


class StaffProfile(models.Model):
    """Payroll configuration for one staff/admin/technician user -- pay
    type, rate, and the daily-hours threshold that defines overtime for
    them. One-to-one with the user; a staff member with no profile yet
    simply isn't included in payroll runs or attendance overtime maths."""

    class PayType(models.TextChoices):
        SALARY = "salary", "Monthly salary"
        HOURLY = "hourly", "Hourly rate"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="staff_profile",
        limit_choices_to={"role__in": ["admin", "support", "sales", "technician", "management", "accounts"]},
    )
    employee_number = models.CharField(max_length=20, unique=True, editable=False)
    # Identity/licensing details -- name and surname already live on the
    # User (first_name/last_name), and a contact number on User.phone;
    # these two are the pieces payroll doesn't otherwise capture, added
    # so staff who drive company vehicles have their ID and driver's
    # license on file here.
    id_number = models.CharField(max_length=30, blank=True, help_text="National ID / passport number.")
    license_number = models.CharField(max_length=30, blank=True, help_text="Driver's license number.")
    pay_type = models.CharField(max_length=10, choices=PayType.choices, default=PayType.HOURLY)
    monthly_salary = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    hourly_rate = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    # Hours per day before attendance starts counting as overtime for this
    # person -- configurable per employee rather than a single global
    # constant, since some staff may be on different shift patterns.
    standard_daily_hours = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal("8.00"))
    overtime_multiplier = models.DecimalField(max_digits=3, decimal_places=2, default=Decimal("1.50"))
    # Running leave balances, in days. Defaults follow the BCEA's usual
    # annual/sick/family-responsibility entitlements as a starting point --
    # an admin can adjust these at onboarding or at the start of a new
    # cycle. A LeaveRequest deducts from the matching balance only once
    # it's approved (see LeaveRequest and the `approve` action).
    annual_leave_balance = models.DecimalField(max_digits=5, decimal_places=1, default=Decimal("21.0"))
    sick_leave_balance = models.DecimalField(max_digits=5, decimal_places=1, default=Decimal("30.0"))
    family_responsibility_leave_balance = models.DecimalField(max_digits=5, decimal_places=1, default=Decimal("3.0"))
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["employee_number"]

    def save(self, *args, **kwargs):
        if not self.employee_number:
            last = StaffProfile.objects.order_by("-id").first()
            next_num = (last.id + 1) if last else 1
            self.employee_number = f"EMP-{next_num:04d}"
        super().save(*args, **kwargs)

    def hourly_equivalent_rate(self):
        """The rate overtime is priced at. For hourly staff this is just
        their rate; for salaried staff it's derived from the monthly
        salary assuming a standard 5-day work week at their configured
        daily hours (a common, simple basis -- not a substitute for a
        proper payroll/tax system)."""
        if self.pay_type == self.PayType.HOURLY:
            return self.hourly_rate or Decimal("0")
        if not self.monthly_salary:
            return Decimal("0")
        monthly_hours = self.standard_daily_hours * 5 * Decimal("4.333")
        if monthly_hours == 0:
            return Decimal("0")
        return (self.monthly_salary / monthly_hours).quantize(Decimal("0.01"))

    def __str__(self):
        return f"{self.employee_number} — {self.user.get_full_name() or self.user.username}"


class AttendanceRecord(models.Model):
    """One clock-in/clock-out pair for one staff member on one day.
    Normally created by the staff member themselves via the clock-in/
    clock-out actions; `is_manual` marks a record an admin added or
    corrected by hand (backfilling a missed clock-out, paper timesheets,
    etc.)."""

    staff = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="attendance_records",
        limit_choices_to={"role__in": ["admin", "support", "sales", "technician", "management", "accounts"]},
    )
    date = models.DateField()
    clock_in = models.DateTimeField()
    clock_out = models.DateTimeField(null=True, blank=True)
    notes = models.CharField(max_length=255, blank=True)
    is_manual = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-clock_in"]

    @property
    def worked_hours(self):
        if not self.clock_out:
            return Decimal("0.00")
        delta = self.clock_out - self.clock_in
        hours = Decimal(delta.total_seconds()) / Decimal(3600)
        return hours.quantize(Decimal("0.01"))

    def regular_hours(self, standard_daily_hours):
        return min(self.worked_hours, standard_daily_hours).quantize(Decimal("0.01"))

    def overtime_hours(self, standard_daily_hours):
        worked = self.worked_hours
        if worked <= standard_daily_hours:
            return Decimal("0.00")
        return (worked - standard_daily_hours).quantize(Decimal("0.01"))

    def __str__(self):
        return f"{self.staff} — {self.date}"


class PayrollRun(models.Model):
    """A payroll calculation for one pay period. Lines are derived from
    attendance records at generation time (see payroll/services.py) and
    can be recalculated while still draft; finalizing locks it in."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        FINALIZED = "finalized", "Finalized"

    period_start = models.DateField()
    period_end = models.DateField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    finalized_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-period_start", "-id"]

    def __str__(self):
        return f"Payroll {self.period_start} – {self.period_end}"


class PayrollRunLine(models.Model):
    """One staff member's computed pay for a PayrollRun. Snapshotted at
    generation time -- rates/hours here don't move if the staff profile
    changes afterwards, so a finalized run stays an accurate record of
    what was actually paid."""

    payroll_run = models.ForeignKey(PayrollRun, on_delete=models.CASCADE, related_name="lines")
    staff = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="payroll_lines")
    pay_type = models.CharField(max_length=10, choices=StaffProfile.PayType.choices)
    regular_hours = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    overtime_hours = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    hourly_rate = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    overtime_rate = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    base_pay = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    overtime_pay = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    gross_pay = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    # --- additions to gross ------------------------------------------------
    # Anything paid on top of the calculated hours: a bonus, a commission, a
    # travel allowance. One field with a description rather than a fixed set,
    # because the set is never right for long -- and it prints on the payslip
    # under whatever name is given here.
    additional_amount = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        help_text="Bonus, commission, allowance — anything paid on top of the hours worked.",
    )
    additional_description = models.CharField(
        max_length=100, blank=True, help_text='What the extra amount is for, e.g. "December bonus".',
    )

    # --- deductions --------------------------------------------------------
    # PAYE is ENTERED, not calculated. It depends on the annual SARS tax
    # tables, the employee's age rebate and any medical aid credits -- none of
    # which this system knows, and all of which change every February. A
    # computed guess would put a wrong statutory figure on a legal document,
    # so the figure comes from whoever does the tax.
    paye = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        verbose_name="PAYE",
        help_text="Income tax withheld, from your accountant or SARS eFiling. Not calculated here — see the model docstring.",
    )
    # UIF *is* calculated: 1% of remuneration up to a monthly ceiling, a rule
    # stable enough to encode. Both the rate and the ceiling live on
    # PayrollSettings so they can be corrected without a code change.
    uif_employee = models.DecimalField(
        max_digits=10, decimal_places=2, default=0, verbose_name="UIF (employee)",
    )
    other_deduction_amount = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        help_text="Loan repayment, garnishee, staff purchase — anything else withheld.",
    )
    other_deduction_description = models.CharField(max_length=100, blank=True)

    # --- employer-side, shown for information only -------------------------
    # Neither of these is withheld from the employee, so neither affects net
    # pay. They appear on the payslip because an employee is entitled to see
    # what is being contributed on their behalf, and because you need the
    # figures for the monthly EMP201.
    uif_employer = models.DecimalField(
        max_digits=10, decimal_places=2, default=0, verbose_name="UIF (employer)",
    )
    sdl = models.DecimalField(
        max_digits=10, decimal_places=2, default=0, verbose_name="SDL",
        help_text="Skills Development Levy — employer cost, not an employee deduction.",
    )

    notes = models.CharField(max_length=255, blank=True, help_text="Printed on the payslip.")

    class Meta:
        ordering = ["staff__first_name", "staff__last_name"]

    def __str__(self):
        return f"{self.staff} — {self.payroll_run}"

    # --- derived figures ---------------------------------------------------
    # Properties, not stored columns: they are pure arithmetic over the fields
    # above, and storing them would let a payslip disagree with its own line
    # after an edit. The payslip, the CSV export and the run totals all read
    # these, so there is one definition of "net pay".

    @property
    def total_earnings(self):
        return self.gross_pay + self.additional_amount

    @property
    def total_deductions(self):
        return self.paye + self.uif_employee + self.other_deduction_amount

    @property
    def net_pay(self):
        return self.total_earnings - self.total_deductions

    @property
    def employer_contributions(self):
        return self.uif_employer + self.sdl

    @property
    def cost_to_company(self):
        return self.total_earnings + self.employer_contributions


class PayrollSettings(models.Model):
    """Singleton (pk=1) for the statutory rates a payslip needs.

    Editable rather than hardcoded because every one of these numbers is set
    by legislation and moves: the UIF ceiling has been raised several times,
    and a hardcoded value silently under-deducts from the month it changes.
    Same singleton pattern as billing.BillingDefaults / notifications.EmailSettings.

    Defaults are the values in force when this was written. CONFIRM THEM
    against the current SARS/Department of Employment and Labour figures
    before running a real payroll -- they are a starting point, not advice.
    """

    uif_rate_pct = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("1.00"),
        help_text="Employee UIF contribution, % of remuneration. Employer contributes the same again.",
    )
    uif_monthly_ceiling = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("17712.00"),
        help_text="UIF is only charged on remuneration up to this amount per month. Check the current figure.",
    )
    sdl_rate_pct = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("1.00"),
        help_text="Skills Development Levy, % of payroll. Employer cost only.",
    )
    sdl_applicable = models.BooleanField(
        default=False,
        help_text="SDL only applies once your annual payroll exceeds the registration threshold. Off by default.",
    )
    # Printed on the payslip. An employee is entitled to know who paid them
    # and under which registration numbers.
    employer_name = models.CharField(max_length=200, blank=True)
    employer_address = models.CharField(max_length=255, blank=True)
    paye_reference = models.CharField(max_length=30, blank=True, help_text="Your SARS PAYE reference number.")
    uif_reference = models.CharField(max_length=30, blank=True, help_text="Your UIF reference number.")
    payslip_note = models.CharField(
        max_length=255, blank=True,
        help_text="Optional line printed at the bottom of every payslip, e.g. who to contact about a query.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Payroll settings"
        verbose_name_plural = "Payroll settings"

    def __str__(self):
        return "Payroll settings"

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def uif_for(self, remuneration):
        """(employee, employer) UIF on this month's remuneration.

        Both sides are the same rate on the same capped base -- the employer
        matches the employee -- so they are returned together to make it
        impossible to compute one and forget the other.
        """
        base = min(remuneration, self.uif_monthly_ceiling)
        if base <= 0:
            return Decimal("0.00"), Decimal("0.00")
        amount = (base * self.uif_rate_pct / Decimal("100")).quantize(Decimal("0.01"))
        return amount, amount

    def sdl_for(self, remuneration):
        """SDL on this month's remuneration, or zero when not registered.

        Uncapped, unlike UIF, and never deducted from the employee.
        """
        if not self.sdl_applicable or remuneration <= 0:
            return Decimal("0.00")
        return (remuneration * self.sdl_rate_pct / Decimal("100")).quantize(Decimal("0.01"))


def days_in_month(a_date):
    return calendar.monthrange(a_date.year, a_date.month)[1]


class LeaveRequest(models.Model):
    """A staff member's request for time off -- annual, sick, or family
    responsibility leave, the three BCEA leave categories this system
    tracks. Starts pending; an admin approves or rejects it. Approving
    deducts the days requested from the matching balance on the staff
    member's profile (see the `approve` action in views.py) -- a
    rejected or still-pending request never touches the balance."""

    class LeaveType(models.TextChoices):
        ANNUAL = "annual", "Annual leave"
        SICK = "sick", "Sick leave"
        FAMILY_RESPONSIBILITY = "family_responsibility", "Family responsibility leave"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    staff = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="leave_requests",
        limit_choices_to={"role__in": ["admin", "support", "sales", "technician", "management", "accounts"]},
    )
    leave_type = models.CharField(max_length=25, choices=LeaveType.choices)
    start_date = models.DateField()
    end_date = models.DateField()
    reason = models.CharField(max_length=255, blank=True)
    # Supporting document -- in practice mainly used for sick leave (a
    # doctor's note/certificate), but left generic in case a family
    # responsibility request ever needs to attach proof too.
    attachment = models.FileField(
        upload_to="leave_attachments/%Y/%m/", null=True, blank=True, validators=ATTACHMENT_VALIDATORS,
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    decision_note = models.CharField(max_length=255, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-start_date", "-id"]

    @property
    def days_requested(self):
        """Weekdays (Mon-Fri) between start and end, inclusive -- the
        usual way working days of leave are counted."""
        if self.end_date < self.start_date:
            return 0
        count = 0
        current = self.start_date
        one_day = timedelta(days=1)
        while current <= self.end_date:
            if current.weekday() < 5:
                count += 1
            current += one_day
        return count

    def balance_field_name(self):
        return {
            self.LeaveType.ANNUAL: "annual_leave_balance",
            self.LeaveType.SICK: "sick_leave_balance",
            self.LeaveType.FAMILY_RESPONSIBILITY: "family_responsibility_leave_balance",
        }[self.leave_type]

    def __str__(self):
        return f"{self.staff} — {self.get_leave_type_display()} ({self.start_date} to {self.end_date})"
