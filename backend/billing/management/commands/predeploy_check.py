"""Read-only. Answers "what will change the moment this deploy serves?"

Nothing in this file writes anything. It exists because several fixes on
this branch change BEHAVIOUR rather than just correcting it, and the
honest way to deploy them is to know the blast radius first. Same spirit
as radius_doctor and balance_drift: look before you touch.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "Read-only pre-deploy report: what this release changes about live data."

    def handle(self, *args, **options):
        from accounts.models import User
        from billing.models import CustomerBillingConfig, Invoice
        from billing.recurring import _add_period
        from expenses.models import Expense
        from network.models import IPPool

        today = timezone.localdate()
        w = self.stdout.write
        w(self.style.MIGRATE_HEADING("Pre-deploy report (nothing is written)"))

        # 1. Recurring billing now advances from the DUE date, so a config
        # left behind by a missed run is caught up instead of skipped.
        behind = []
        for config in CustomerBillingConfig.objects.select_related("customer").filter(
            billing_enabled=True, auto_create_invoices=True, next_billing_date__isnull=False
        ):
            if _add_period(config.next_billing_date, config.payment_period) <= today:
                behind.append((config.customer.full_name, config.next_billing_date))
        w("")
        w(f"1. Billing configs more than one period behind: {len(behind)}")
        for name, due in behind[:20]:
            w(f"     {name[:34]:34} due since {due}")
        if len(behind) > 20:
            w(f"     ... and {len(behind) - 20} more")
        w("   These were being SKIPPED and are now caught up, one invoice per Run.")
        w("   Use Finance -> Recurring Billing -> Preview first: it writes nothing.")

        # 2. The partner rule is now applied on endpoints that missed it.
        restricted = User.objects.exclude(allowed_partners=[]).exclude(role=User.Role.ADMIN)
        w("")
        w(f"2. Non-admin staff restricted to partners: {restricted.count()}")
        for user in restricted[:20]:
            w(f"     {user.username[:24]:24} {user.role:12} partners={user.allowed_partners}")
        w("   These accounts lose access to OTHER partners' tickets, jobs,")
        w("   statement PDFs, speed-now and deletion requests. Intended --")
        w("   but it is what they will phone about.")

        # 3. Suspended-customer blocking now targets the walled-garden address.
        pools = IPPool.objects.filter(category=IPPool.Category.WALLED_GARDEN).count()
        w("")
        w(f"3. Walled-garden IP pools configured: {pools}")
        w("   0 means suspended-customer blocking keeps its old behaviour.")
        w("   1 or more means block entries move to the walled-garden address")
        w("   on the next router sync -- which is the fix.")

        # 4. The VAT return now keys on date_issued.
        missing = Invoice.objects.filter(
            date_issued__isnull=True, status__in=["unpaid", "paid", "overdue"]
        ).count()
        w("")
        style = self.style.SUCCESS if missing == 0 else self.style.ERROR
        w(style(f"4. Chargeable invoices with no issue date: {missing} (must be 0)"))
        if missing:
            w("   The 0021 backfill has not run, or ran before this code was serving.")

        # 5. Historical input VAT on wages -- not corrected by deploying.
        wages = Expense.objects.filter(category=Expense.Category.SALARIES).exclude(vat_rate_pct=0)
        claimed = sum((e.vat_amount for e in wages), start=0)
        w("")
        w(f"5. Salaries expenses still claiming input VAT: {wages.count()} (R{claimed})")
        w("   Historical. This release stops NEW ones; it does not amend these.")
        w("   Worth the accountant's eyes before the next VAT201.")
        w("")
        w("Also, and no query can tell you: none of the RADIUS/MikroTik work has")
        w("run against real hardware. First apply_speed_policies run is capped by")
        w("--limit (default 50), which now applies to disconnected lines too.")
