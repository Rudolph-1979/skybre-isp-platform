"""Open the invoicing approval gate for tests that are not about it.

Package 9 moved the Preview -> Approve gate into the engine
(billing.recurring.approval_for), so a committed run with no approved
preview now withholds invoicing. That is right in production and it is
tested on its own terms in test_billing_approval.py. But dozens of older
engine tests -- late runs, one customer failing, interrupted runs, the
invoice email balance, discount projections -- commit a run to check
something else entirely, and they began reading "0 invoices" because
nobody had approved anything.

Those tests are about what an APPROVED run does, so this mixin stands in
for the approval: approval_for answers "ok" for the life of each test.
It patches only the engine's own lookup; the gate's behaviour stays
covered by test_billing_approval.py, which does not use this.

Put it first in the bases:

    class LateRunTests(ApprovalGateOpenMixin, RecurringBillingRunTestCase):
"""
from unittest import mock

APPROVED = {
    "ok": True,
    "reason": "",
    "snapshot": None,
    "approved_fingerprint": None,
    "current_fingerprint": None,
}


class ApprovalGateOpenMixin:
    # Class-level rather than setUp: most of these test classes define their
    # own setUp without calling super(), which would silently skip a
    # setUp-based patch and leave the gate shut.
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        patcher = mock.patch(
            "billing.recurring.approval_for", side_effect=lambda *a, **k: dict(APPROVED)
        )
        patcher.start()
        cls.addClassCleanup(patcher.stop)
