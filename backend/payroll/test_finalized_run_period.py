"""A finalized payroll run's period, and what a PATCH could still do to it.

Finalizing is what makes a run the record of what was actually paid: the
lines can no longer be edited, the run can't be recalculated and it can't be
deleted. The period it covers was the one field nobody guarded, so a PATCH
could move a signed-off August run onto July's dates -- the payslips already
handed to staff and the bank CSV already sent to FNB would then name a period
the run no longer claims, and SARS reconciliation has nothing to tie them to.

The same PATCH also had to survive being partial: sending one date raised
KeyError inside validate(), which the caller saw as a 500.
"""
import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from payroll.models import PayrollRun

User = get_user_model()


class FinalizedRunPeriodTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(
            username="payroll-admin", password="pw-for-tests", role=User.Role.ADMIN
        )
        self.client.force_authenticate(self.admin)
        self.run = PayrollRun.objects.create(
            period_start=datetime.date(2026, 8, 1),
            period_end=datetime.date(2026, 8, 31),
            generated_by=self.admin,
        )

    def _finalize(self):
        response = self.client.post(f"/api/payroll-runs/{self.run.id}/finalize/")
        self.assertEqual(response.status_code, 200, response.data)

    def test_a_finalized_runs_period_cannot_be_moved(self):
        """The payslips are already out; the run must keep saying August."""
        self._finalize()
        response = self.client.patch(
            f"/api/payroll-runs/{self.run.id}/",
            {"period_start": "2026-07-01", "period_end": "2026-07-31"},
            format="json",
        )
        self.assertEqual(response.status_code, 403, response.data)
        self.run.refresh_from_db()
        self.assertEqual(self.run.period_start, datetime.date(2026, 8, 1))
        self.assertEqual(self.run.period_end, datetime.date(2026, 8, 31))

    def test_a_draft_runs_period_can_still_be_corrected(self):
        """Only finalizing locks it -- a draft typed in with the wrong month
        must still be fixable in place."""
        response = self.client.patch(
            f"/api/payroll-runs/{self.run.id}/", {"period_end": "2026-09-04"}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.run.refresh_from_db()
        self.assertEqual(self.run.period_end, datetime.date(2026, 9, 4))

    def test_moving_one_date_past_the_other_is_still_rejected(self):
        """A partial PATCH used to raise KeyError -> 500. It must come back
        as the ordinary validation error, and the run must not move: an end
        date before the start is a period nothing can be paid for."""
        response = self.client.patch(
            f"/api/payroll-runs/{self.run.id}/", {"period_end": "2026-07-15"}, format="json"
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.run.refresh_from_db()
        self.assertEqual(self.run.period_end, datetime.date(2026, 8, 31))
