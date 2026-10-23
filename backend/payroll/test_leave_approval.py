"""Approving a leave request must take the days off exactly once.

approve() read the request unlocked, checked it was PENDING, deducted the
balance, and then saved the decision as a separate write. Two admins
clicking Approve at once -- or one double-clicking -- both passed the
check and both deducted. And a failure between the two writes left the
days gone with the request still PENDING, so approving it again took them
a second time.
"""
import datetime
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TransactionTestCase
from rest_framework.test import APIClient

from payroll.models import LeaveRequest, StaffProfile

User = get_user_model()


class LeaveApprovalTests(TransactionTestCase):
    """TransactionTestCase because the point is what survives a rollback,
    which a wrapping test transaction would hide."""

    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(username="boss", password="pw-for-tests", role="admin")
        self.staff = User.objects.create_user(username="thandi", password="pw-for-tests", role="support")
        self.profile = StaffProfile.objects.create(
            user=self.staff, pay_type=StaffProfile.PayType.SALARY,
            monthly_salary=Decimal("30000.00"), annual_leave_balance=Decimal("15.0"),
        )
        self.leave = LeaveRequest.objects.create(
            staff=self.staff, leave_type=LeaveRequest.LeaveType.ANNUAL,
            start_date=datetime.date(2026, 10, 1), end_date=datetime.date(2026, 10, 5),
        )
        self.days = Decimal(self.leave.days_requested)
        self.client.force_authenticate(self.admin)

    def _approve(self):
        return self.client.post(f"/api/leave-requests/{self.leave.pk}/approve/")

    def _balance(self):
        self.profile.refresh_from_db()
        return self.profile.annual_leave_balance

    def test_approving_takes_the_days_off_once(self):
        response = self._approve()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self._balance(), Decimal("15.0") - self.days)

    def test_approving_twice_does_not_take_them_twice(self):
        self._approve()
        second = self._approve()
        self.assertEqual(second.status_code, 400, second.data)
        self.assertEqual(self._balance(), Decimal("15.0") - self.days)

    def test_a_failure_partway_through_leaves_the_balance_alone(self):
        """The deduction and the decision have to move together. Deducting
        the days and then failing to record the approval left the request
        PENDING with the balance already down -- so approving it again,
        which is exactly what an admin would do, took the days twice."""
        original = LeaveRequest.save

        def fail_on_the_decision(self_, *args, **kwargs):
            if "status" in (kwargs.get("update_fields") or []):
                raise RuntimeError("crash after the balance was written")
            return original(self_, *args, **kwargs)

        with mock.patch.object(LeaveRequest, "save", fail_on_the_decision):
            with self.assertRaises(RuntimeError):
                self._approve()

        self.leave.refresh_from_db()
        self.assertEqual(self.leave.status, LeaveRequest.Status.PENDING)
        self.assertEqual(self._balance(), Decimal("15.0"),
                         "the days were taken off for an approval that never completed")

        response = self._approve()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self._balance(), Decimal("15.0") - self.days,
                         "retrying the approval took the days a second time")
