# Dates from Splynx (21 Oct 2026, Rudolph: "Splynx added date VS Vexar Added
# date and Splynx service start date VS Vexar Sevice start date?").
#
#   1. Client added date: Splynx "Date added" -> Vexar client's sign-up date
#      (the date shown on the Customers list; when blank, Vexar shows the day
#      the client was created here -- the October import).
#   2. Service start date: Splynx "Internet services start date" -> the Vexar
#      service's start date.
#
# Reads /tmp/splynx_added_dates.csv and /tmp/splynx_start_dates.csv, written by
# splynx_audit.py (run that first). DRY RUN by default -- changes nothing,
# writes the plan to /tmp/splynx_dates_plan.csv. With APPLY=1 it changes them:
#   docker compose exec -T backend python manage.py shell < splynx_dates_fix.py
#   docker compose exec -T -e APPLY=1 backend python manage.py shell < splynx_dates_fix.py
#
# Changed only when ALL of these hold:
#   * Vexar's date is in October 2026 (the import), and still what the audit
#     saw (not edited since);
#   * Splynx's date is earlier than Vexar's and not in the future;
#   * (services) the service doesn't end on or before the new date.
# Everything else is listed as "left alone" with the reason.
#
# Billing: a service start date only stops a service being billed while it is
# in the future, so moving it EARLIER bills nothing extra and nothing
# backdated. The sign-up date is information only (Customers list, reports).
# Saved with a plain UPDATE -- no router/RADIUS sync for 900+ lines, which the
# dates mean nothing to -- and each change goes on the History tab as
# "Splynx dates fix".
import csv
import datetime
import os

from django.db import transaction
from django.utils import timezone

from audit.context import acting_as_system
from audit.tracking import record
from billing.models import Service
from customers.models import Customer

ADDED = os.environ.get("SPLYNX_ADDED", "/tmp/splynx_added_dates.csv")
STARTS = os.environ.get("SPLYNX_DATES", "/tmp/splynx_start_dates.csv")
PLAN = os.environ.get("SPLYNX_PLAN", "/tmp/splynx_dates_plan.csv")
APPLY = os.environ.get("APPLY") == "1"
IMPORT_MONTH = (2026, 10)
today = timezone.localdate()


def day(v):
    try:
        return datetime.datetime.strptime(str(v).strip(), "%d/%m/%Y").date()
    except ValueError:
        return None


def why_not(current, seen, sp):
    if current != seen:
        return f"changed since the audit (now {current:%d/%m/%Y})" if current else "cleared since the audit"
    if (seen.year, seen.month) != IMPORT_MONTH:
        return "Vexar date is not an October import date"
    if sp is None:
        return "no Splynx date"
    if sp > today:
        return "Splynx date is in the future"
    if sp >= seen:
        return "Splynx date is not earlier"
    return ""


def read(path):
    return list(csv.DictReader(open(path, encoding="utf-8"))) if os.path.exists(path) else []


plan, client_changes, service_changes = [], [], []

# 1. Client added date.
added_rows = read(ADDED)
customers = Customer.objects.in_bulk([int(r["Customer id"]) for r in added_rows])
for r in added_rows:
    c = customers.get(int(r["Customer id"]))
    sp, seen = day(r["Splynx added"]), day(r["Vexar added"])
    if c is None:
        reason = "client no longer exists"
    else:
        reason = why_not(c.signup_date or timezone.localtime(c.created_at).date(), seen, sp)
    plan.append({"What": "Client added date", "Vexar client": r["Vexar client"], "Name": r["Name"], "Service login": "",
                 "Splynx": r["Splynx added"], "Vexar": r["Vexar added"],
                 "Action": "left alone" if reason else "CHANGE", "Reason": reason})
    if not reason:
        client_changes.append((c, sp))

# 2. Service start date.
start_rows = read(STARTS)
services = Service.objects.select_related("customer", "tariff").in_bulk([int(r["Service id"]) for r in start_rows])
for r in start_rows:
    s = services.get(int(r["Service id"]))
    sp, seen = day(r["Splynx start"]), day(r["Vexar start"])
    if s is None:
        reason = "service no longer exists"
    else:
        reason = why_not(s.start_date, seen, sp)
        if not reason and s.end_date and s.end_date <= sp:
            reason = f"service ends {s.end_date:%d/%m/%Y}"
    plan.append({"What": "Service start date", "Vexar client": r["Vexar client"], "Name": r["Name"],
                 "Service login": r["Service login"], "Splynx": r["Splynx start"], "Vexar": r["Vexar start"],
                 "Action": "left alone" if reason else "CHANGE", "Reason": reason})
    if not reason:
        service_changes.append((s, sp))

with open(PLAN, "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=["What", "Vexar client", "Name", "Service login", "Splynx", "Vexar", "Action", "Reason"])
    w.writeheader()
    w.writerows(plan)


def by_year(changes):
    years = {}
    for _, d in changes:
        years[d.year] = years.get(d.year, 0) + 1
    return ", ".join(f"{y}: {n}" for y, n in sorted(years.items())) or "-"


print(f"\nSPLYNX DATES FIX {'(APPLYING)' if APPLY else '(DRY RUN -- nothing changed)'} {today:%d/%m/%Y}")
print(f"  Client added date:  {len(added_rows)} differ, {len(client_changes)} to change "
      f"(new dates by year {by_year(client_changes)})")
print(f"  Service start date: {len(start_rows)} differ, {len(service_changes)} to change "
      f"(new dates by year {by_year(service_changes)})")
print("\nFirst 10 of each:")
for c, d in client_changes[:10]:
    cur = c.signup_date or timezone.localtime(c.created_at).date()
    print(f"  client   {c.customer_id:<14} {c.full_name[:26]:<26} {'':<30} {cur:%d/%m/%Y} -> {d:%d/%m/%Y}")
for s, d in service_changes[:10]:
    print(f"  service  {s.customer.customer_id:<14} {s.customer.full_name[:26]:<26} {s.radius_username[:30]:<30} "
          f"{s.start_date:%d/%m/%Y} -> {d:%d/%m/%Y}")
left = [p for p in plan if p["Action"] != "CHANGE"]
if left:
    print(f"\nLeft alone ({len(left)}):")
    for p in left[:40]:
        print(f"  {p['What'][:7]:<8} {p['Vexar client']:<14} {p['Name'][:26]:<26} {p['Service login'][:30]:<30} "
              f"Splynx {p['Splynx']}  Vexar {p['Vexar']}  -- {p['Reason']}")
    if len(left) > 40:
        print(f"  ... and {len(left) - 40} more in the plan file")

if APPLY and (client_changes or service_changes):
    with transaction.atomic(), acting_as_system("Splynx dates fix"):
        for c, d in client_changes:
            old = c.signup_date
            Customer.objects.filter(pk=c.pk).update(signup_date=d)
            record("updated", c, changes=[{"field": "signup_date", "label": "Signup date",
                                            "from": str(old) if old else "", "to": str(d)}],
                   detail="Sign-up date set from Splynx 'Date added' (audit 21 Oct 2026)", customer=c)
        for s, d in service_changes:
            old = s.start_date
            Service.objects.filter(pk=s.pk, start_date=old).update(start_date=d)
            s.start_date = d
            record("updated", s, changes=[{"field": "start_date", "label": "Start date", "from": str(old), "to": str(d)}],
                   detail="Start date set from Splynx (audit 21 Oct 2026)", customer=s.customer)
    print(f"\nDONE: {len(client_changes)} client added dates and {len(service_changes)} service start dates changed.")
elif not APPLY:
    print("\nNothing changed. To apply, run again with -e APPLY=1.")
print(f"Full plan: {PLAN}")
