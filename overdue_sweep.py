# Overdue sweep (21 Oct 2026, Rudolph: "Can we do a sweep to see which clients
# has been overdue and if they have received notifications?").
# READ ONLY -- changes nothing. Run inside the backend container:
#   docker compose exec -T backend python manage.py shell < overdue_sweep.py
# Writes /tmp/overdue_sweep.csv in the container and prints a summary.
import csv
import datetime

from django.db.models import Count, Max, Min, Q
from django.utils import timezone

from billing import recurring as r
from billing.models import CustomerBillingConfig, Invoice, ReminderSettings, Service, SuspensionSettings
from customers.models import Customer
from notifications.models import EmailLog

today = timezone.localdate()
rs = ReminderSettings.load()
auto = SuspensionSettings.load().auto_suspend_enabled
OWED = [Invoice.Status.UNPAID, Invoice.Status.OVERDUE]

# Everyone with an unpaid invoice past its due date, or a line suspended for non-payment.
overdue_ids = set(
    Invoice.objects.filter(status__in=OWED, date_due__lt=today).values_list("customer_id", flat=True)
) | set(
    Service.objects.filter(status=Service.Status.SUSPENDED, suspended_for_nonpayment=True)
    .values_list("customer_id", flat=True)
)

rows, flags_count = [], {}
for c in Customer.objects.filter(pk__in=overdue_ids).select_related("partner").order_by("customer_id"):
    g = CustomerBillingConfig.for_customer(c)
    owed = c.invoices.filter(status__in=OWED)
    agg = owed.aggregate(n=Count("id"), oldest=Min("date_due"), last_rem=Max("last_reminder_run_date"))
    overdue_n = owed.filter(date_due__lt=today).count()
    start = r.countdown_start(c, g, as_of=today) if g.blocking_period_days is not None else None
    try:
        block = r.suspension_block_date(c, g, today)
    except Exception:  # noqa: BLE001 -- a report must not stop on one client
        block = None
    since = start or agg["oldest"]
    logs = EmailLog.objects.filter(customer=c)
    if since:
        logs = logs.filter(created_at__date__gte=since)

    def count(key, status=EmailLog.Status.SENT):
        return logs.filter(template_key=key, status=status).count()

    def last(key):
        d = logs.filter(template_key=key, status=EmailLog.Status.SENT).aggregate(d=Max("created_at"))["d"]
        return timezone.localtime(d).date() if d else None

    rem_sent, rem_failed = count("payment_reminder"), count("payment_reminder", EmailLog.Status.FAILED)
    warn_sent, warn_failed = count("suspension_warning"), count("suspension_warning", EmailLog.Status.FAILED)
    susp_sent = count("suspension")
    services = list(c.services.exclude(status=Service.Status.TERMINATED).values_list("status", "suspended_for_nonpayment"))
    line = ("suspended (non-payment)" if any(s == "suspended" and np for s, np in services)
            else "suspended (other)" if any(s == "suspended" for s, _ in services)
            else "active" if any(s == "active" for s, _ in services) else "no active line")
    has_email = bool((c.email or "").strip() or (getattr(c, "billing_email", "") or "").strip())
    offsets = sorted(r.reminder_offsets(g, rs))
    ext = g.suspension_extended_until if g.suspension_extended_until and g.suspension_extended_until >= today else None

    flags = []
    if not g.billing_enabled:
        flags.append("billing off")
    if g.blocking_period_days is None:
        flags.append("no blocking period")
    if not has_email:
        flags.append("no email address")
    if not g.reminder_enabled or not offsets:
        flags.append("reminders off")
    if not g.send_billing_notifications:
        flags.append("billing emails off")
    if c.balance <= g.minimum_balance:
        flags.append("under minimum balance")
    if rem_failed or warn_failed:
        flags.append("email send FAILED")
    if line == "suspended (non-payment)" and rem_sent == 0:
        flags.append("SUSPENDED WITHOUT A REMINDER")
    if line == "suspended (non-payment)" and susp_sent == 0:
        flags.append("no suspension email")
    if line == "active" and c.balance > g.minimum_balance and start and rem_sent == 0 and has_email and offsets:
        flags.append("over minimum, no reminder yet")
    if ext:
        flags.append(f"extension to {ext:%d/%m/%Y}")
    for f in flags:
        key = f.split(" to ")[0] if f.startswith("extension") else f
        flags_count[key] = flags_count.get(key, 0) + 1

    rows.append({
        "Client": c.customer_id, "Name": c.full_name, "Partner": c.partner.name if c.partner_id else "",
        "Balance": f"{c.balance:.2f}", "Minimum": f"{g.minimum_balance:.2f}",
        "Unpaid invoices": agg["n"], "Overdue invoices": overdue_n,
        "Oldest due": agg["oldest"] or "", "Countdown from": start or "",
        "Blocking days": g.blocking_period_days if g.blocking_period_days is not None else "",
        "Cut-off date": block or "", "Line": line, "Email on file": "yes" if has_email else "NO",
        "Reminder days": " ".join(str(o) for o in offsets) or "off",
        "Reminders sent": rem_sent, "Last reminder": last("payment_reminder") or agg["last_rem"] or "",
        "Reminders failed": rem_failed, "Warnings sent": warn_sent, "Last warning": last("suspension_warning") or "",
        "Warnings failed": warn_failed, "Suspension emails": susp_sent,
        "Flags": "; ".join(flags),
    })

path = "/tmp/overdue_sweep.csv"
with open(path, "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["Client"])
    w.writeheader()
    w.writerows(rows)

print(f"\nOVERDUE SWEEP {today:%d/%m/%Y} -- {len(rows)} clients overdue or suspended for non-payment "
      f"(auto-suspension {'ON' if auto else 'OFF'}). Notifications counted since each client's countdown started.")
print(f"  suspended for non-payment: {sum(1 for x in rows if x['Line'] == 'suspended (non-payment)')}")
print(f"  received at least one reminder: {sum(1 for x in rows if x['Reminders sent'])}")
print(f"  received at least one suspension warning: {sum(1 for x in rows if x['Warnings sent'])}")
for k, v in sorted(flags_count.items(), key=lambda kv: -kv[1]):
    print(f"  {k}: {v}")
print("\nNeeds a look:")
for x in rows:
    if any(t in x["Flags"] for t in ("SUSPENDED WITHOUT", "FAILED", "no reminder yet", "no email address")):
        print(f"  {x['Client']:<16} {x['Name'][:26]:<26} bal {x['Balance']:>9}  {x['Line']:<24} "
              f"rem {x['Reminders sent']} warn {x['Warnings sent']}  {x['Flags']}")
print(f"\nFull list: {path} ({len(rows)} rows)")
