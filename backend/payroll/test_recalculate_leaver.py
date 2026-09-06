"""A leaver's line on a draft payroll run, and what Recalculate did to it.

Recalculate deleted every line and rebuilt them from the currently-active
staff, so anyone deactivated between generating the draft and recalculating
it lost their line entirely -- and with it their final pay and whatever PAYE
the accountant had already typed against it. The run simply came back one
person shorter, with nothing on the page to say so, and the bank CSV and the
payslip PDFs are both driven off those lines. That is exactly the person it
must not happen to: someone deactivated mid-draft has almost always just
left, and this is their last payslip.
"""
import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from payroll.models import AttendanceRecord, PayrollRun, StaffProfile
from payroll.services import generate_payroll_run_lines

User = get_user_model()


class RecalculateKeepsLeaversTests(TestCase):
    def setUp(self):
        self.stayer = self._staff("stayer")
        self.leaver = self._staff("leaver")
        # Deactivated long before this run's period -- nothing on this run
        # belongs to them, and recalculating must not invent a line.
        self.long_gone = self._staff("long_gone")
        self.long_gone.staff_profile.is_active = False
        self.long_gone.staff_profile.save(update_fields=["is_active"])

        self._attend(self.stayer, datetime.date(2026, 8, 10), 8)
        self._attend(self.leaver, datetime.date(2026, 8, 10), 8)
        self.run = PayrollRun.objects.create(
            period_start=datetime.date(2026, 8, 1), period_end=datetime.date(2026, 8, 31)
        )
        generate_payroll_run_lines(self.run)

    def _staff(self, username):
        user = User.objects.create_user(
            username=username, password="pw-for-tests", role=User.Role.TECHNICIAN
        )
        StaffProfile.objects.create(
            user=user, pay_type=StaffProfile.PayType.HOURLY, hourly_rate=Decimal("100.00")
        )
        return user

    def _attend(self, user, day, hours):
        clock_in = timezone.make_aware(datetime.datetime.combine(day, datetime.time(8, 0)))
        AttendanceRecord.objects.create(
            staff=user, date=day, clock_in=clock_in,
            clock_out=clock_in + datetime.timedelta(hours=hours),
        )

    def _resign(self, user):
        """What the Employees page does when someone leaves."""
        user.staff_profile.is_active = False
        user.staff_profile.save(update_fields=["is_active"])

    def _staff_on_run(self):
        return sorted(line.staff.username for line in self.run.lines.all())

    def test_a_staff_member_deactivated_after_the_draft_keeps_their_line(self):
        """The leaver stays on the run; the person who left before the period
        ever started is still not added to it."""
        self._resign(self.leaver)
        generate_payroll_run_lines(self.run)
        self.assertEqual(self._staff_on_run(), ["leaver", "stayer"])
    def test_a_leavers_typed_paye_and_notes_survive_the_recalculation(self):
        """PAYE is typed in by hand from eFiling. Losing it means the final
        payslip under-deducts tax, and nothing says the figure went missing."""
        line = self.run.lines.get(staff=self.leaver)
        line.paye = Decimal("1234.00")
        line.notes = "Final pay - last day 14 August"
        line.save(update_fields=["paye", "notes"])

        self._resign(self.leaver)
        generate_payroll_run_lines(self.run)

        line = self.run.lines.get(staff=self.leaver)
        self.assertEqual(line.paye, Decimal("1234.00"))
        self.assertEqual(line.notes, "Final pay - last day 14 August")
    def test_a_leaver_whose_login_was_disabled_keeps_their_line(self):
        """Some admins switch the user account off rather than the payroll
        profile. Either way the person is owed their final pay."""
        self.leaver.is_active = False
        self.leaver.save(update_fields=["is_active"])
        generate_payroll_run_lines(self.run)
        self.assertEqual(self._staff_on_run(), ["leaver", "stayer"])
    def test_a_leavers_corrected_attendance_is_still_recalculated(self):
        """Keeping the line must not freeze it -- correcting a leaver's last
        week and hitting Recalculate is the whole reason the button exists."""
        self._resign(self.leaver)
        self._attend(self.leaver, datetime.date(2026, 8, 11), 8)
        generate_payroll_run_lines(self.run)
        line = self.run.lines.get(staff=self.leaver)
        self.assertEqual(line.regular_hours, Decimal("16.00"))
        self.assertEqual(line.gross_pay, Decimal("1600.00"))
    def test_recalculating_from_the_payroll_page_returns_the_leavers_line(self):
        """End to end, because the page redraws itself from this response --
        a dropped line disappears from the table without a word."""
        self._resign(self.leaver)
        client = APIClient()
        client.force_authenticate(
            User.objects.create_user(username="boss", password="pw-for-tests", role=User.Role.ADMIN)
        )
        res = client.post(f"/api/payroll-runs/{self.run.id}/recalculate/")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["staff_count"], 2)
        self.assertIn("leaver", [line["staff_name"] for line in res.data["lines"]])


class RemovingALineFromADraftTests(TestCase):
    """Taking somebody off a draft run, now that Recalculate keeps them.

    Recalculate holds on to a leaver's line on purpose -- that is what the
    tests above are for -- which means nothing else on the page can drop a
    person any more. The case that needs it is the resignation nobody has
    processed yet: a technician's last day is 31 July, HR has not switched
    the profile off when the August draft is generated, so the draft pays
    them a full month's salary. The figures on the line are all derived and
    read-only, so it cannot be zeroed either; without an explicit removal the
    only way out is deleting the whole draft and retyping everyone's PAYE,
    and the thing that gets missed goes out on the bank CSV.
    """

    def setUp(self):
        self.admin = User.objects.create_user(
            username="boss", password="pw-for-tests", role=User.Role.ADMIN
        )
        self.stayer = self._salaried("stayer")
        self.leaver = self._salaried("leaver")
        self.run = PayrollRun.objects.create(
            period_start=datetime.date(2026, 8, 1), period_end=datetime.date(2026, 8, 31)
        )
        generate_payroll_run_lines(self.run)
        self.client_ = APIClient()
        self.client_.force_authenticate(self.admin)

    def _salaried(self, username):
        user = User.objects.create_user(
            username=username, password="pw-for-tests", role=User.Role.TECHNICIAN
        )
        StaffProfile.objects.create(
            user=user, pay_type=StaffProfile.PayType.SALARY,
            monthly_salary=Decimal("30000.00"),
        )
        return user

    def _staff_on_run(self):
        return sorted(line.staff.username for line in self.run.lines.all())

    def test_a_leaver_can_be_taken_off_a_draft_run(self):
        """The August draft is paying R30,000 to somebody who left in July.
        Somebody has to be able to remove that line."""
        line = self.run.lines.get(staff=self.leaver)
        self.assertEqual(line.base_pay, Decimal("30000.00"))
        res = self.client_.delete(f"/api/payroll-run-lines/{line.id}/")
        self.assertEqual(res.status_code, 204, getattr(res, "data", res))
        self.assertEqual(self._staff_on_run(), ["stayer"])

    def test_a_removed_leaver_does_not_come_back_on_the_next_recalculation(self):
        """Removing the line only helps if Recalculate leaves it removed --
        and everyone else's typed PAYE has to survive that recalculation."""
        stayer_line = self.run.lines.get(staff=self.stayer)
        stayer_line.paye = Decimal("4321.00")
        stayer_line.save(update_fields=["paye"])

        self.leaver.staff_profile.is_active = False
        self.leaver.staff_profile.save(update_fields=["is_active"])
        self.client_.delete(f"/api/payroll-run-lines/{self.run.lines.get(staff=self.leaver).id}/")

        res = self.client_.post(f"/api/payroll-runs/{self.run.id}/recalculate/")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(self._staff_on_run(), ["stayer"])
        self.assertEqual(self.run.lines.get(staff=self.stayer).paye, Decimal("4321.00"))

    def test_a_line_on_a_finalised_run_cannot_be_removed(self):
        """A finalised run is the record of what was actually paid. Deleting a
        line out of it would leave the EMP201 and the bank export disagreeing
        about who got paid, so a correction belongs in a new run."""
        line = self.run.lines.get(staff=self.leaver)
        self.client_.post(f"/api/payroll-runs/{self.run.id}/finalize/")
        res = self.client_.delete(f"/api/payroll-run-lines/{line.id}/")
        self.assertEqual(res.status_code, 403, getattr(res, "data", res))
        self.assertEqual(self._staff_on_run(), ["leaver", "stayer"])
