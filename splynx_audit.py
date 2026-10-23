# Splynx -> Vexar audit, fresh approach (21 Oct 2026, Skybre Customers list 26).
# READ ONLY -- changes nothing. Shows real problems only:
#   * ACT skipped ("ACT should be excluded"); no finance checks ("Were ignoring
#     finance for now, only services etc").
#   * Splynx Inactive clients last online before 1 Oct 2026 skipped ("On Splynx
#     ignore Inactive clients before October"). Inactive since October (moved to
#     Vexar, no plans left in Splynx): only checked that they exist in Vexar.
#   * Naming-only differences ignored (MTD Networx = MTD, SW 6 Mbps = Skybre
#     Wireless 6 Mbps, 30mbps BF Vuma = Vuma BF 30/30 Mbps, names).
#   * A Splynx line and a Vexar line left over on the same client are paired and
#     shown as one "Service differs" row, not as a missing + an extra.
#   * Start dates go to their own files: service start (Splynx "Internet
#     services start date" vs the Vexar service's start date) and client added
#     date (Splynx "Date added" vs Vexar's sign-up date, or the date the client
#     was created in Vexar when no sign-up date is set).
#   * Login lookup ("Login Lookup please"): a Splynx line whose login is on a
#     DIFFERENT Vexar client is shown as "Service on another Vexar client"
#     (with that client), not as missing. Terminated services count too.
# Run:
#   docker compose cp splynx_customers.csv backend:/tmp/splynx_customers.csv
#   docker compose exec -T backend python manage.py shell < splynx_audit.py
# Writes /tmp/splynx_audit.csv, /tmp/splynx_start_dates.csv and
# /tmp/splynx_added_dates.csv in the container.
import csv
import datetime
import os
import re
from collections import Counter, defaultdict

from django.utils import timezone

from billing.models import Service
from customers.models import Customer

SRC = os.environ.get("SPLYNX_SRC", "/tmp/splynx_customers.csv")
OUT = os.environ.get("SPLYNX_OUT", "/tmp/splynx_audit.csv")
OUT_DATES = os.environ.get("SPLYNX_OUT_DATES", "/tmp/splynx_start_dates.csv")
OUT_ADDED = os.environ.get("SPLYNX_OUT_ADDED", "/tmp/splynx_added_dates.csv")
INACTIVE_CUTOFF = datetime.date(2026, 10, 1)
EXCLUDED_PARTNERS = {"act"}
today = timezone.localdate()


def norm(v):
    return re.sub(r"[^a-z0-9]", "", str(v or "").lower())


def split(v):
    return [x.strip() for x in str(v or "").replace("\xa0", " ").split(",")
            if x.strip() and x.strip().lower() != "none"]


def day(v):
    try:
        return datetime.datetime.strptime(str(v).strip()[:10], "%d/%m/%Y").date()
    except ValueError:
        return None


def partner_key(v):
    p = norm(v)
    for suffix in ("networx", "fibre"):
        if p.endswith(suffix) and p != suffix:
            p = p[: -len(suffix)]
    return p


def plan_key(v):
    s = str(v or "").lower().replace("skybre wireless", "sw")
    toks = re.findall(r"[a-z]+|[0-9]+", s)
    words = frozenset(t for t in toks if t.isalpha() and t not in {"mbps", "bf", "fibre"})
    nums = frozenset(t for t in toks if t.isdigit())
    return words, nums


SECTIONS = [
    ("1 Client missing in Vexar", "Online/active in Splynx, not found in Vexar"),
    ("2 Moved client missing in Vexar", "Inactive in Splynx since October, not found in Vexar"),
    ("3 Service on another Vexar client", "Splynx line's login is on a different Vexar client"),
    ("4 Service missing in Vexar", "Splynx line with no Vexar service anywhere"),
    ("5 Service differs", "Same client, line differs in plan and/or login"),
    ("6 Extra service in Vexar", "Vexar service with no Splynx line on that client"),
    ("7 No login in Vexar", "Vexar service has no PPPoE/service login"),
    ("8 Service still Pending in Vexar", "Splynx line is live, Vexar still Pending"),
    ("9 Status differs", "Client status doesn't match"),
    ("10 Partner differs", "Different partner (naming ignored)"),
    ("11 In Vexar, not in Splynx", "Vexar client not on the Splynx list at all"),
]
ORDER = {name: i for i, (name, _) in enumerate(SECTIONS)}

all_rows = list(csv.DictReader(open(SRC, encoding="utf-8")))
rows, skipped = [], Counter()
for r in all_rows:
    if partner_key(r["Partner"]) in EXCLUDED_PARTNERS:
        skipped["ACT"] += 1
        continue
    if r["Status"].strip().lower() == "inactive":
        last = day(r.get("Last online", ""))
        if last is None or last < INACTIVE_CUTOFF:
            skipped["Inactive before October"] += 1
            continue
    rows.append(r)

customers = list(Customer.objects.select_related("partner").all())
by_id = {norm(c.customer_id): c for c in customers if c.customer_id}
by_id_part = {}
for c in customers:
    for part in str(c.customer_id or "").split("_"):
        if norm(part):
            by_id_part.setdefault(norm(part), c)
by_name = defaultdict(list)
for c in customers:
    by_name[norm(c.full_name)].append(c)
services = defaultdict(list)
for s in Service.objects.select_related("tariff", "customer").exclude(status=Service.Status.TERMINATED).order_by("pk"):
    services[s.customer_id].append(s)
by_login = {norm(s.radius_username): s.customer for lst in services.values() for s in lst if s.radius_username}
# Every Vexar service by login, terminated ones included (for the login lookup).
svc_by_login = defaultdict(list)
for s in Service.objects.select_related("tariff", "customer").exclude(radius_username="").order_by("pk"):
    if s.radius_username:
        svc_by_login[norm(s.radius_username)].append(s)
# Which Splynx client has each login.
splynx_login_owner = {}
for r in rows:
    for l in split(r["Service login"]):
        splynx_login_owner.setdefault(norm(l), r["Portal login"])
holds_lines_of = defaultdict(set)   # Vexar customer pk -> Splynx clients whose lines it has

LIVE = {"online", "active", "online last 24 hours", "new"}
STATUS_OK = {
    "online": {"online", "active"}, "active": {"online", "active"}, "online last 24 hours": {"online", "active"},
    "blocked": {"suspended", "bad_debt", "online", "active"}, "new": {"new", "active", "online"},
}

findings, dates, added = [], [], []
added_seen = set()
matched = set()


def add(section, row, cust, splynx="", vexar="", note=""):
    findings.append({
        "Section": section,
        "Splynx client": row["Portal login"] if row else "",
        "Vexar client": cust.customer_id if cust else "",
        "Name": (row["Full name"] if row else "") or (cust.full_name if cust else ""),
        "Splynx": splynx, "Vexar": vexar, "Note": note,
    })


def tariff(s):
    return s.tariff.name if s.tariff_id else "(no tariff)"


for row in rows:
    login = row["Portal login"]
    sp_status = row["Status"].strip().lower()
    moved = sp_status == "inactive"
    cust = by_id.get(norm(login))
    if cust is None:
        for part in [login] + login.split("_"):
            cust = by_id.get(norm(part)) or by_id_part.get(norm(part))
            if cust:
                break
    plans, logins, starts = split(row["Internet plans"]), split(row["Service login"]), split(row["Internet services start date"])
    if cust is None:
        for l in logins:
            if norm(l) in by_login:
                cust = by_login[norm(l)]
                break
    if cust is None and len(by_name.get(norm(row["Full name"]), [])) == 1:
        cust = by_name[norm(row["Full name"])][0]
    if cust is None:
        add("2 Moved client missing in Vexar" if moved else "1 Client missing in Vexar", row, None,
            f"{row['Status']} · {row['Partner']} · {row['Internet plans'] or 'no plan'}",
            note=f"last online {row.get('Last online', '')[:10]}" if moved else "")
        continue
    matched.add(cust.pk)

    sp_added = day(row.get("Date added", ""))
    vx_added = cust.signup_date or timezone.localtime(cust.created_at).date()
    if sp_added and sp_added != vx_added and cust.pk not in added_seen:
        added_seen.add(cust.pk)
        added.append({
            "Splynx client": login, "Vexar client": cust.customer_id, "Name": row["Full name"], "Customer id": cust.pk,
            "Splynx added": f"{sp_added:%d/%m/%Y}", "Vexar added": f"{vx_added:%d/%m/%Y}",
            "Vexar source": "sign-up date" if cust.signup_date else "created date",
        })

    vx_partner = cust.partner.name if cust.partner_id else ""
    if row["Partner"] and partner_key(vx_partner) != partner_key(row["Partner"]):
        add("10 Partner differs", row, cust, row["Partner"], vx_partner or "(none)")
    if not moved and cust.status not in STATUS_OK.get(sp_status, {cust.status}):
        add("9 Status differs", row, cust, row["Status"], cust.get_status_display())

    if moved:
        continue  # Splynx has no lines left for these; nothing to compare.

    sp_lines = [{"plan": p, "login": logins[i] if i < len(logins) else "",
                 "start": day(starts[i]) if i < len(starts) else None} for i, p in enumerate(plans)]
    vx = list(services.get(cust.pk, []))
    pairs, left_sp = [], []
    # 1) login on this client, 2) login on ANOTHER Vexar client, 3) plan,
    # 4) whatever is left on both sides.
    for line in sp_lines:
        s = next((x for x in vx if line["login"] and norm(x.radius_username) == norm(line["login"])), None)
        if s:
            vx.remove(s)
            pairs.append((line, s))
            continue
        elsewhere = [x for x in svc_by_login.get(norm(line["login"]), []) if x.customer_id != cust.pk] if line["login"] else []
        if elsewhere:
            live = [x for x in elsewhere if x.status != Service.Status.TERMINATED] or elsewhere
            x = live[0]
            holds_lines_of[x.customer_id].add(login)
            add("3 Service on another Vexar client", row, cust, f"{line['plan']} · {line['login']}",
                f"{x.customer.customer_id} {x.customer.full_name[:24]} · {tariff(x)} · {x.get_status_display()}",
                "plan differs" if plan_key(tariff(x)) != plan_key(line["plan"]) else "")
            continue
        left_sp.append(line)
    # A Vexar service whose login Splynx has under another client is that
    # client's line: never pair it by plan or by leftovers here.
    free = [x for x in vx if splynx_login_owner.get(norm(x.radius_username), login) == login]
    still = []
    for line in left_sp:
        s = next((x for x in free if plan_key(tariff(x)) == plan_key(line["plan"])), None)
        if s:
            vx.remove(s)
            free.remove(s)
            pairs.append((line, s))
        else:
            still.append(line)
    for line in still:
        if free:
            s = free.pop(0)
            vx.remove(s)
            pairs.append((line, s))
        else:
            gone = [x for x in svc_by_login.get(norm(line["login"]), []) if x.customer_id == cust.pk] if line["login"] else []
            add("4 Service missing in Vexar", row, cust, f"{line['plan']} · {line['login'] or 'no login'}",
                f"{tariff(gone[0])} · Terminated" if gone else "", "terminated in Vexar" if gone else "")
    for s in vx:
        owner = splynx_login_owner.get(norm(s.radius_username)) if s.radius_username else None
        add("6 Extra service in Vexar", row, cust, "",
            f"{tariff(s)} · {s.radius_username or 'no login'} · {s.get_status_display()}",
            f"Splynx has it under {owner}" if owner and owner != login else "")

    for line, s in pairs:
        diffs = []
        if plan_key(tariff(s)) != plan_key(line["plan"]):
            diffs.append("plan")
        if line["login"] and s.radius_username and norm(s.radius_username) != norm(line["login"]):
            diffs.append("login")
        if diffs:
            add("5 Service differs", row, cust, f"{line['plan']} · {line['login'] or 'no login'}",
                f"{tariff(s)} · {s.radius_username or 'no login'}", " + ".join(diffs))
        if not (s.radius_username or "").strip():
            add("7 No login in Vexar", row, cust, line["login"], tariff(s))
        if s.status == Service.Status.PENDING and sp_status in LIVE:
            add("8 Service still Pending in Vexar", row, cust, row["Status"], tariff(s))
        if line["start"] and s.start_date and line["start"] != s.start_date:
            dates.append({
                "Splynx client": login, "Vexar client": cust.customer_id, "Name": row["Full name"],
                "Service login": s.radius_username or "", "Plan": tariff(s), "Service id": s.pk,
                "Splynx start": f"{line['start']:%d/%m/%Y}", "Vexar start": f"{s.start_date:%d/%m/%Y}",
            })

for c in customers:
    if c.pk in matched or c.status == "inactive":
        continue
    if c.partner_id and partner_key(c.partner.name) in EXCLUDED_PARTNERS:
        continue
    held = sorted(holds_lines_of.get(c.pk, ()))
    add("11 In Vexar, not in Splynx", None, c, "",
        f"{c.get_status_display()} · {c.partner.name if c.partner_id else 'no partner'} · "
        f"created {timezone.localtime(c.created_at):%d/%m/%Y}",
        f"has Splynx line(s) of {', '.join(held)}" if held else "")

findings.sort(key=lambda f: (ORDER[f["Section"]], f["Splynx client"] or f["Vexar client"]))
with open(OUT, "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=["Section", "Splynx client", "Vexar client", "Name", "Splynx", "Vexar", "Note"])
    w.writeheader()
    w.writerows(findings)
with open(OUT_ADDED, "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=["Splynx client", "Vexar client", "Name", "Customer id",
                                       "Splynx added", "Vexar added", "Vexar source"])
    w.writeheader()
    w.writerows(added)
with open(OUT_DATES, "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=["Splynx client", "Vexar client", "Name", "Service login", "Plan",
                                       "Service id", "Splynx start", "Vexar start"])
    w.writeheader()
    w.writerows(dates)

counts = Counter(f["Section"] for f in findings)
moved_n = sum(1 for r in rows if r["Status"].strip().lower() == "inactive")
print(f"\nSPLYNX AUDIT {today:%d/%m/%Y}: {len(all_rows)} on the list, {len(rows)} checked "
      f"({len(rows) - moved_n} live, {moved_n} inactive since October), {len(matched)} found in Vexar.")
print("Skipped: " + (", ".join(f"{v} {k}" for k, v in skipped.items()) or "none") + ". Finance and naming not checked.\n")
for name, desc in SECTIONS:
    n = counts.get(name, 0)
    print(f"{name}: {n}  ({desc})")
    if 0 < n <= 60:
        for f in findings:
            if f["Section"] == name:
                print(f"    {f['Splynx client'] or '-':<18} {f['Vexar client'] or '-':<14} {f['Name'][:26]:<26} "
                      f"{f['Splynx'][:44]:<44} {f['Vexar'][:58]:<58} {f['Note']}")
oct_ = sum(1 for d in dates if d["Vexar start"].endswith("/10/2026"))
print(f"\nService start dates differ on {len(dates)} services ({oct_} have an October 2026 date in Vexar): {OUT_DATES}")
oct_a = sum(1 for d in added if d["Vexar added"].endswith("/10/2026"))
print(f"Client added dates differ on {len(added)} clients ({oct_a} have an October 2026 date in Vexar): {OUT_ADDED}")
print(f"Every finding: {OUT} ({len(findings)} rows)")
