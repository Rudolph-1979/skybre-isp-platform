"""Administration -> Revenue: six charts (Rudolph/George, 9 Oct 2026).

  * mrr_series          MRR and ARPU over time (daily / weekly / monthly / yearly)
  * top_payers          the biggest payers this month or last month
  * daily_payments      money received per day, this month or last month
  * invoicing_series    invoices raised per month / quarter / year (incl. VAT),
                        split paid / unpaid, and the paid part on time / late
  * payments_series     payments per month / quarter / year (incl. VAT) by method

Partner filter, everywhere: keys are partner ids as strings plus "direct".
Empty = every partner the user may see. A partner-restricted user only ever
gets their own partners (and Direct when that is ticked for them).

  * MRR / ARPU count LINES, so a line counts under its own partner
    (Service.partner, else the client's) -- the same rule as Clients per
    partner.
  * Invoices and payments belong to a CLIENT, not a line, so a client
    matches when its own partner or any of its live lines' partners is
    selected.

MRR history (George chose "snapshot + backfill"):
  * Today is always worked out live, and saved (RevenueSnapshot).
  * A past day with a snapshot uses it -- exact.
  * A past day without one is ESTIMATED from service start/end dates at
    today's tariff prices, and flagged `estimated` so the chart can say so.
    The estimate counts a line from its start date (else the day it was
    created) until its end date; lines that are terminated with no end date
    can't be placed in time and are left out of the past, and so are lines
    that are suspended or pending today. Tariff changes over
    time aren't known, so the past uses today's price.
MRR itself = tariff price of ACTIVE lines, excl. VAT -- the dashboard's MRR.
ARPU = MRR / distinct clients with at least one counted line.
"""
import calendar
import datetime
from collections import defaultdict
from decimal import Decimal

from django.db.models import Q, Sum
from django.utils import timezone

from billing.models import CreditAllocation, CreditNoteAllocation, Invoice, Payment, PaymentAllocation, Service

from .models import Customer, Partner, RevenueSnapshot
from .scoping import DIRECT, allowed_partner_ids, is_partner_restricted, sees_direct

ZERO = Decimal("0.00")
DIRECT_KEY = "direct"


def _q(value):
    return Decimal(value).quantize(Decimal("0.01"))


def _money(value):
    return f"{_q(value or 0):.2f}"


# --- partners ------------------------------------------------------------

def _key_int(key):
    """'direct' -> 0, '12' -> 12."""
    return DIRECT if key == DIRECT_KEY else int(key)


def _key_str(pid):
    return DIRECT_KEY if not pid else str(pid)


def partner_options(user):
    """The partners this user may filter on, Direct first then A-Z."""
    restricted = is_partner_restricted(user)
    allowed = set(allowed_partner_ids(user))
    out = []
    if sees_direct(user):
        out.append({"key": DIRECT_KEY, "name": "Direct (no partner)"})
    for p in Partner.objects.order_by("name").only("pk", "name"):
        if not restricted or p.pk in allowed:
            out.append({"key": str(p.pk), "name": p.name})
    return out


def selected_keys(user, raw):
    """The partner keys (ints, 0 = Direct) to include: the ?partner= list,
    narrowed to what the user may see. Unknown keys are an error."""
    allowed = {_key_int(o["key"]) for o in partner_options(user)}
    picked = [v.strip() for v in (raw or "").split(",") if v.strip()]
    if not picked:
        return allowed
    keys = set()
    for v in picked:
        try:
            k = _key_int(v)
        except ValueError:
            raise ValueError(f"Unknown partner: {v}")
        if k not in allowed:
            raise ValueError(f"Unknown partner: {v}")
        keys.add(k)
    return keys


def _customer_ids_for(keys):
    """Clients whose own partner, or any live line's partner, is in `keys`."""
    ids = [k for k in keys if k != DIRECT]
    cond = Q(partner_id__in=ids)
    line_q = Q(partner_id__in=ids)
    if DIRECT in keys:
        cond |= Q(partner__isnull=True)
    with_line = Service.objects.filter(line_q).exclude(status=Service.Status.TERMINATED).values("customer_id")
    cond |= Q(pk__in=with_line)
    return Customer.objects.filter(cond).values("pk")


# --- MRR / ARPU ------------------------------------------------------------

def _lines():
    """Every service that could ever count, with what the estimate needs."""
    return list(
        Service.objects.select_related("tariff", "customer")
        .only("pk", "customer_id", "partner_id", "status", "start_date", "end_date", "created_at",
              "tariff__price", "customer__partner_id")
    )


def _line_key(svc):
    return svc.partner_id or svc.customer.partner_id or DIRECT


def live_figures():
    """{partner key: (mrr, services, client ids)} as of right now."""
    out = defaultdict(lambda: [ZERO, 0, set()])
    for svc in Service.objects.filter(status=Service.Status.ACTIVE).select_related("tariff", "customer").only(
        "pk", "customer_id", "partner_id", "tariff__price", "customer__partner_id"
    ):
        row = out[_line_key(svc)]
        row[0] += Decimal(svc.tariff.price or 0)
        row[1] += 1
        row[2].add(svc.customer_id)
    return out


def take_snapshot(day=None):
    """Save today's live figures (one row per partner key). Idempotent for
    the day: re-running replaces that day's rows."""
    day = day or timezone.localdate()
    figures = live_figures()
    RevenueSnapshot.objects.filter(date=day).exclude(partner_key__in=list(figures)).delete()
    for key, (mrr, services, clients) in figures.items():
        RevenueSnapshot.objects.update_or_create(
            date=day, partner_key=key,
            defaults={"mrr": _q(mrr), "services": services, "client_ids": sorted(clients)},
        )
    return figures


def _estimate(lines, day, keys):
    mrr, clients = ZERO, set()
    for svc in lines:
        if _line_key(svc) not in keys:
            continue
        # Only lines billing today, or ended on a known date, can be placed
        # in the past: a pending line hasn't started, and a suspended one was
        # suspended on a day nothing records -- counting either would put a
        # step between the estimate and today's exact figure.
        if svc.status in (Service.Status.PENDING, Service.Status.SUSPENDED):
            continue
        start = svc.start_date or (timezone.localtime(svc.created_at).date() if svc.created_at else None)
        if start is None or start > day:
            continue
        if svc.end_date is not None:
            if svc.end_date <= day:
                continue
        elif svc.status == Service.Status.TERMINATED:
            continue
        mrr += Decimal(svc.tariff.price or 0)
        clients.add(svc.customer_id)
    return mrr, clients


def _period_ends(period, today):
    """(label, last day) for each point, oldest first."""
    points = []
    if period == "daily":
        for i in range(29, -1, -1):
            d = today - datetime.timedelta(days=i)
            points.append((d.strftime("%d/%m"), d))
    elif period == "weekly":
        for i in range(25, -1, -1):
            d = today - datetime.timedelta(weeks=i)
            points.append((d.strftime("%d/%m/%y"), d))
    elif period == "yearly":
        for i in range(4, -1, -1):
            y = today.year - i
            points.append((str(y), today if y == today.year else datetime.date(y, 12, 31)))
    else:  # monthly
        y, m = today.year, today.month
        months = []
        for _ in range(18):
            months.append((y, m))
            m -= 1
            if m == 0:
                y, m = y - 1, 12
        for y, m in reversed(months):
            last = datetime.date(y, m, calendar.monthrange(y, m)[1])
            points.append((datetime.date(y, m, 1).strftime("%b %Y"), min(last, today)))
    return points


def mrr_series(user, period, keys):
    today = timezone.localdate()
    live = take_snapshot(today)
    points = _period_ends(period, today)
    days = [d for _label, d in points]
    snaps = defaultdict(dict)
    for s in RevenueSnapshot.objects.filter(date__in=days, partner_key__in=list(keys)):
        snaps[s.date][s.partner_key] = s
    lines = None
    out = []
    for label, day in points:
        if day == today:
            mrr, clients = ZERO, set()
            for k in keys:
                if k in live:
                    mrr += live[k][0]
                    clients |= live[k][2]
            estimated = False
        elif snaps.get(day) or RevenueSnapshot.objects.filter(date=day).exists():
            # A snapshot day: exact, even for a partner with no row (it had
            # no active lines that day).
            mrr = sum((s.mrr for s in snaps.get(day, {}).values()), ZERO)
            clients = set()
            for s in snaps.get(day, {}).values():
                clients |= set(s.client_ids)
            estimated = False
        else:
            if lines is None:
                lines = _lines()
            mrr, clients = _estimate(lines, day, keys)
            estimated = True
        n = len(clients)
        out.append({
            "label": label,
            "date": day.isoformat(),
            "mrr": _money(mrr),
            "clients": n,
            "arpu": _money(mrr / n) if n else "0.00",
            "estimated": estimated,
        })
    return {"period": period, "points": out}



# --- outstanding / overdue by payment method ---------------------------------

NO_METHOD_KEY = "none"


def outstanding_by_method(user, keys):
    """What clients owe right now, incl. VAT, per payment method (George,
    12 Oct 2026). Same rules as Finance -> Invoices and the Finance
    dashboard, so the numbers agree: owed = unpaid or overdue status,
    amount = total - paid (never below 0), overdue = owed and past its due
    date. The method is the one on the client's billing setup
    (CustomerBillingConfig.payment_method); none = "No payment method set".
    Partner filter: by client, like the other money charts."""
    from billing.dashboard import OUTSTANDING
    from billing.models import CustomerBillingConfig
    from billing.overdue import owed_q, today

    as_of = today()
    method_of = {
        row["customer_id"]: (row["payment_method_id"], row["payment_method__name"])
        for row in CustomerBillingConfig.objects.values("customer_id", "payment_method_id", "payment_method__name")
    }
    groups = {}
    total_clients = set()
    rows = (
        Invoice.objects.filter(owed_q(), customer_id__in=_customer_ids_for(keys))
        .annotate(owed=OUTSTANDING)
        .filter(owed__gt=0)
        .values("customer_id", "owed", "date_due")
    )
    for inv in rows:
        mid, name = method_of.get(inv["customer_id"], (None, None))
        key = str(mid) if mid else NO_METHOD_KEY
        g = groups.setdefault(key, {"key": key, "name": name or "No payment method set",
                                    "clients": set(), "outstanding": ZERO, "overdue": ZERO})
        g["clients"].add(inv["customer_id"])
        total_clients.add(inv["customer_id"])
        g["outstanding"] += inv["owed"]
        if inv["date_due"] < as_of:
            g["overdue"] += inv["owed"]
    out = sorted(groups.values(), key=lambda g: (g["key"] == NO_METHOD_KEY, g["name"].lower()))
    total_out = sum((g["outstanding"] for g in out), ZERO)
    total_over = sum((g["overdue"] for g in out), ZERO)
    return {
        "as_of": as_of.isoformat(),
        "rows": [
            {"key": g["key"], "name": g["name"], "clients": len(g["clients"]),
             "outstanding": _money(g["outstanding"]), "overdue": _money(g["overdue"])}
            for g in out
        ],
        "total": {"clients": len(total_clients), "outstanding": _money(total_out), "overdue": _money(total_over)},
    }


def outstanding_clients(user, keys, method_key):
    """The clients behind one payment-method row, most overdue first."""
    from billing.dashboard import OUTSTANDING
    from billing.overdue import owed_q, today

    as_of = today()
    qs = Invoice.objects.filter(owed_q(), customer_id__in=_customer_ids_for(keys))
    if method_key == NO_METHOD_KEY:
        qs = qs.filter(Q(customer__billing_config__isnull=True) | Q(customer__billing_config__payment_method__isnull=True))
    else:
        qs = qs.filter(customer__billing_config__payment_method_id=int(method_key))
    per = {}
    for inv in qs.annotate(owed=OUTSTANDING).filter(owed__gt=0).values(
        "customer_id", "customer__full_name", "customer__customer_id", "owed", "date_due"
    ):
        c = per.setdefault(inv["customer_id"], {"id": inv["customer_id"], "full_name": inv["customer__full_name"],
                                                "customer_ref": inv["customer__customer_id"],
                                                "outstanding": ZERO, "overdue": ZERO, "invoices": 0})
        c["outstanding"] += inv["owed"]
        c["invoices"] += 1
        if inv["date_due"] < as_of:
            c["overdue"] += inv["owed"]
    out = sorted(per.values(), key=lambda c: (-c["overdue"], -c["outstanding"], c["full_name"].lower()))
    for c in out:
        c["outstanding"], c["overdue"] = _money(c["outstanding"]), _money(c["overdue"])
    return out


# --- months ------------------------------------------------------------------

def _month_bounds(which, today):
    first = today.replace(day=1)
    if which == "last":
        last = first - datetime.timedelta(days=1)
        first = last.replace(day=1)
    else:
        last = first.replace(day=calendar.monthrange(first.year, first.month)[1])
    return first, last


def _payments(keys, start, end):
    return Payment.objects.filter(
        customer_id__in=_customer_ids_for(keys), date__date__gte=start, date__date__lte=end,
    )


def top_payers(user, which, keys, limit=10):
    first, last = _month_bounds(which, timezone.localdate())
    rows = list(
        _payments(keys, first, last).values("customer_id", "customer__full_name", "customer__customer_id")
        .annotate(total=Sum("amount")).order_by("-total", "customer__full_name")
    )
    grand = sum((r["total"] for r in rows), ZERO)
    top = rows[:limit]
    rest = sum((r["total"] for r in rows[limit:]), ZERO)
    return {
        "month": which,
        "from": first.isoformat(),
        "to": last.isoformat(),
        "total": _money(grand),
        "payers": len(rows),
        "rows": [
            {"customer": r["customer_id"], "name": r["customer__full_name"],
             "customer_ref": r["customer__customer_id"], "amount": _money(r["total"])}
            for r in top
        ],
        "others": {"payers": max(0, len(rows) - limit), "amount": _money(rest)},
    }


def daily_payments(user, which, keys):
    first, last = _month_bounds(which, timezone.localdate())
    by_day = defaultdict(lambda: [ZERO, 0])
    for p in _payments(keys, first, last).only("amount", "date"):
        d = timezone.localtime(p.date).date()
        by_day[d][0] += p.amount
        by_day[d][1] += 1
    days = []
    d = first
    while d <= last:
        amount, count = by_day.get(d, [ZERO, 0])
        days.append({"day": d.day, "date": d.isoformat(), "amount": _money(amount), "payments": count})
        d += datetime.timedelta(days=1)
    return {"month": which, "from": first.isoformat(), "to": last.isoformat(),
            "total": _money(sum((v[0] for v in by_day.values()), ZERO)), "days": days}


# --- month / quarter / year buckets -----------------------------------------

def _buckets(period, today):
    """[(label, first day, last day)] oldest first."""
    out = []
    if period == "year":
        for i in range(4, -1, -1):
            y = today.year - i
            out.append((str(y), datetime.date(y, 1, 1), datetime.date(y, 12, 31)))
    elif period == "quarter":
        q = (today.month - 1) // 3
        y = today.year
        items = []
        for _ in range(8):
            items.append((y, q))
            q -= 1
            if q < 0:
                y, q = y - 1, 3
        for y, q in reversed(items):
            first = datetime.date(y, q * 3 + 1, 1)
            last_month = q * 3 + 3
            out.append((f"Q{q + 1} {y}", first, datetime.date(y, last_month, calendar.monthrange(y, last_month)[1])))
    else:  # month
        y, m = today.year, today.month
        items = []
        for _ in range(12):
            items.append((y, m))
            m -= 1
            if m == 0:
                y, m = y - 1, 12
        for y, m in reversed(items):
            out.append((datetime.date(y, m, 1).strftime("%b %Y"), datetime.date(y, m, 1),
                        datetime.date(y, m, calendar.monthrange(y, m)[1])))
    return out


def _bucket_index(buckets):
    def find(day):
        for i, (_label, first, last) in enumerate(buckets):
            if first <= day <= last:
                return i
        return None
    return find


def invoicing_series(user, period, keys):
    """Invoices raised in each bucket (by issue date, else created date),
    incl. VAT. Credit notes, quotes, pro formas, drafts and cancelled
    documents are left out.

      paid / unpaid       paid_amount vs what is still owed
      on_time / late      the paid part, by when it was settled: each
                          allocation or direct payment is on time when it
                          came in on or before the due date
      paid_unknown        paid, but nothing records when (e.g. imported
                          as paid) -- shown, never guessed into on time/late
    """
    buckets = _buckets(period, timezone.localdate())
    find = _bucket_index(buckets)
    start, end = buckets[0][1], buckets[-1][2]
    invoices = list(
        Invoice.objects.filter(customer_id__in=_customer_ids_for(keys), status__in=Invoice.ISSUED_STATUSES)
        .exclude(number__startswith="CN")
        .filter(Q(date_issued__range=(start, end)) | Q(date_issued__isnull=True, date_created__range=(start, end)))
        .only("pk", "total", "paid_amount", "date_due", "date_issued", "date_created")
    )
    settled = defaultdict(list)  # invoice id -> [(date, amount)]
    ids = [i.pk for i in invoices]
    for a in PaymentAllocation.objects.filter(invoice_id__in=ids).select_related("payment").only(
        "invoice_id", "amount", "payment__date"
    ):
        settled[a.invoice_id].append((timezone.localtime(a.payment.date).date(), a.amount))
    for p in Payment.objects.filter(invoice_id__in=ids).only("invoice_id", "amount", "date"):
        settled[p.invoice_id].append((timezone.localtime(p.date).date(), p.amount))
    # Credits and credit notes settle invoices too; they count as paid on
    # the day they were allocated.
    for model in (CreditAllocation, CreditNoteAllocation):
        for c in model.objects.filter(invoice_id__in=ids).only("invoice_id", "amount", "created_at"):
            settled[c.invoice_id].append((timezone.localtime(c.created_at).date(), c.amount))

    rows = [{"label": label, "from": first.isoformat(), "to": last.isoformat(), "invoices": 0,
             "invoiced": ZERO, "paid": ZERO, "unpaid": ZERO, "on_time": ZERO, "late": ZERO, "paid_unknown": ZERO}
            for label, first, last in buckets]
    for inv in invoices:
        i = find(inv.date_issued or inv.date_created)
        if i is None:
            continue
        r = rows[i]
        total, paid = Decimal(inv.total), min(Decimal(inv.paid_amount), Decimal(inv.total))
        r["invoices"] += 1
        r["invoiced"] += total
        r["paid"] += paid
        r["unpaid"] += max(total - paid, ZERO)
        left = paid
        for day, amount in sorted(settled.get(inv.pk, [])):
            take = min(Decimal(amount), left)
            if take <= 0:
                break
            if day <= inv.date_due:
                r["on_time"] += take
            else:
                r["late"] += take
            left -= take
        r["paid_unknown"] += left
    for r in rows:
        for k in ("invoiced", "paid", "unpaid", "on_time", "late", "paid_unknown"):
            r[k] = _money(r[k])
    return {"period": period, "rows": rows}


PAYMENT_METHODS = [(value, label) for value, label in Payment.Method.choices]


def payments_series(user, period, keys):
    buckets = _buckets(period, timezone.localdate())
    find = _bucket_index(buckets)
    start, end = buckets[0][1], buckets[-1][2]
    rows = [{"label": label, "from": first.isoformat(), "to": last.isoformat(), "total": ZERO, "payments": 0,
             **{m: ZERO for m, _l in PAYMENT_METHODS}} for label, first, last in buckets]
    for p in _payments(keys, start, end).only("amount", "date", "method"):
        i = find(timezone.localtime(p.date).date())
        if i is None:
            continue
        method = p.method if p.method in rows[i] else "manual"
        rows[i][method] += p.amount
        rows[i]["total"] += p.amount
        rows[i]["payments"] += 1
    for r in rows:
        for m, _l in PAYMENT_METHODS:
            r[m] = _money(r[m])
        r["total"] = _money(r["total"])
    return {"period": period, "methods": [{"key": m, "label": l} for m, l in PAYMENT_METHODS], "rows": rows}
