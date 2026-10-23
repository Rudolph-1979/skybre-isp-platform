"""The Support page (#212, 22 Oct 2026): Rudolph approved the support
dashboard mockup -- "Ok build it please" -- after asking for "the response
rates for support to clients" to be on it.

ONE CALL (GET /api/support/dashboard/?period=today|week|month) returns the
whole page, built from the same helpers the rest of the platform already
uses, so this page cannot disagree with them:

  * Recently offline = radiusauth.offline.recently_offline (online in the
    last 24 hours, down now; suspended / paused / terminated lines are not
    "offline", they are off on purpose).
  * Cut-off today = billing.recurring.upcoming_blocks for next_run_date(),
    the same rows as the dashboard's "Blocking" panel.
  * Every client list / count is narrowed to the partners the staff member
    may see (customers.scoping), like every other staff view.

Each panel is only filled for staff who hold the section it comes from
(Customers, Finance, Services, Scheduling); the page itself needs Support
Tickets. A panel the person can't see comes back as null and the page
leaves it out.

DEFINITIONS (the parts people will argue about later):

  CLIENT-RAISED TICKET. A customer ticket opened by the client -- from the
  portal, the Conecto app, or before Vexar recorded who opened a ticket
  (created_by empty). A ticket a staff member logged (usually from a phone
  call) is NOT in the first-reply figures: the client was already being
  spoken to, so its "first reply" would be zero minutes and flatter the
  number. Those tickets still count for time to resolve, and the page says
  how many were left out.

  FIRST REPLY. The first comment on the ticket that the client can see (not
  an internal note) written by a staff member, after the ticket was opened.

  WITHIN TARGET. First reply within SupportSettings.reply_target_minutes of
  OFFICE time. A ticket with no reply yet that has already waited longer
  than the target counts as missed; one still inside the target is not
  counted either way yet.

  OFFICE TIME. Only minutes inside office hours on office days, skipping
  public holidays (billing.PublicHoliday). Used for every duration in the
  "Response to clients" section.

  WAITING (ticket queue). Wall-clock time since the client's last contact --
  the ticket being opened, or their latest comment -- that no staff member
  has answered with a visible reply yet. Answered tickets show "Answered".
  A ticket set to Pending Customer is waiting on the client, not on us.
  Wall-clock rather than office time here because it is a queue, and
  "this client has been waiting since yesterday afternoon" is what matters
  when deciding what to pick up next. Red past 12 hours.

  BY STAFF MEMBER. Tickets are counted against who they are assigned to
  now. Admin and Management see every row; everyone else sees the team
  totals and their own row only.

  POSSIBLE OUTAGE. Recently-offline clients grouped by where their line
  connects: the service's access point / sector (Service.access_device +
  access_detail) when that is filled in, else the router it authenticates
  on (Service.device, the NAS), else the NAS address in the last RADIUS
  session. SupportSettings.outage_min_clients or more in one group going
  down within outage_window_minutes of each other is flagged.
"""
import datetime
from collections import defaultdict
from statistics import median

from django.db.models import Q
from django.utils import timezone

from accounts.permissions import user_can_access_section

from .models import SupportSettings, Ticket, TicketComment

PERIODS = ("today", "week", "month")
OPEN_STATUSES = (Ticket.Status.OPEN, Ticket.Status.PENDING)
WAITING_RED_HOURS = 12
LIST_LIMIT = 10
OFFLINE_LIMIT = 8
QUEUE_LIMIT = 100


# ---------------------------------------------------------------------------
# Office time
# ---------------------------------------------------------------------------

def office_minutes(start, end, cfg, holidays):
    """Minutes between two datetimes that fall inside office hours.

    `cfg` is a SupportSettings (or anything with office_start, office_end and
    office_days); `holidays` a set of dates (billing.business_days.holiday_set).
    Local time throughout, so a day is the day staff see on the wall."""
    if start is None or end is None or end <= start:
        return 0.0
    tz = timezone.get_current_timezone()
    start = timezone.localtime(start, tz)
    end = timezone.localtime(end, tz)
    days = set(cfg.office_days or [])
    if cfg.office_end <= cfg.office_start:
        return 0.0
    total = 0.0
    day = start.date()
    last = end.date()
    while day <= last:
        if day.weekday() in days and day not in holidays:
            open_at = timezone.make_aware(datetime.datetime.combine(day, cfg.office_start), tz)
            close_at = timezone.make_aware(datetime.datetime.combine(day, cfg.office_end), tz)
            lo = max(start, open_at)
            hi = min(end, close_at)
            if hi > lo:
                total += (hi - lo).total_seconds() / 60
        day += datetime.timedelta(days=1)
    return total


def _median(values):
    return round(median(values), 1) if values else None


def _person(user):
    if user is None:
        return ""
    name = f"{user.first_name} {user.last_name}".strip()
    return name or user.username


# ---------------------------------------------------------------------------
# Tickets: who said what, and when
# ---------------------------------------------------------------------------

def _is_client_raised(ticket):
    creator = ticket.created_by
    return creator is None or not creator.is_staff_member


def _comment_timeline(ticket_ids):
    """{ticket_id: {"staff": [datetimes], "client": [datetimes]}} for the
    comments the client can see, oldest first. One query."""
    out = defaultdict(lambda: {"staff": [], "client": []})
    if not ticket_ids:
        return out
    rows = (
        TicketComment.objects.filter(ticket_id__in=ticket_ids, is_internal=False)
        .values("ticket_id", "created_at", "author_id", "author__role", "external_author")
        .order_by("created_at")
    )
    for row in rows:
        if row["author_id"] is not None:
            kind = "client" if row["author__role"] == "customer" else "staff"
        elif row["external_author"]:
            # A reply from the Conecto app: the client, who has no account.
            kind = "client"
        else:
            continue  # generated by the platform -- nobody's reply
        out[row["ticket_id"]][kind].append(row["created_at"])
    return out


def _first_staff_reply(ticket, timeline):
    for when in timeline.get(ticket.pk, {}).get("staff", []):
        if when > ticket.created_at:
            return when
    return None


def _scoped_customer_tickets(user):
    from customers.scoping import scope_by_customer

    qs = Ticket.objects.filter(is_internal=False).select_related("customer", "assigned_to", "created_by")
    return scope_by_customer(qs, user, allow_null=False)


def _visible_customers(user):
    from customers.models import Customer
    from customers.scoping import partner_condition

    qs = Customer.objects.all()
    condition = partner_condition(user)
    return qs if condition is None else qs.filter(condition)


# ---------------------------------------------------------------------------
# Line status for the queue
# ---------------------------------------------------------------------------

def _line_status(customer_ids):
    """{customer_id: "online"|"offline"|"active"|"suspended"|"paused"|"pending"|None}."""
    from billing.models import Service
    from customers.models import Customer
    from radiusauth.offline import connection_state

    statuses = defaultdict(set)
    for cid, status in (
        Service.objects.filter(customer_id__in=customer_ids)
        .exclude(status=Service.Status.TERMINATED)
        .values_list("customer_id", "status")
    ):
        statuses[cid].add(status)
    state = connection_state(list(Customer.objects.filter(pk__in=customer_ids))) if customer_ids else {}
    out = {}
    for cid in customer_ids:
        have = statuses.get(cid, set())
        if Service.Status.ACTIVE in have:
            s = state.get(cid)
            out[cid] = "active" if s is None else ("online" if s["online"] else "offline")
        elif Service.Status.SUSPENDED in have:
            out[cid] = "suspended"
        elif Service.Status.PAUSED in have:
            out[cid] = "paused"
        elif Service.Status.PENDING in have:
            out[cid] = "pending"
        else:
            out[cid] = None
    return out


# ---------------------------------------------------------------------------
# The queue
# ---------------------------------------------------------------------------

def ticket_queue(user, now):
    tickets = list(
        _scoped_customer_tickets(user).filter(status__in=OPEN_STATUSES).order_by("created_at")[:500]
    )
    timeline = _comment_timeline([t.pk for t in tickets])
    lines = _line_status(list({t.customer_id for t in tickets}))
    rows = []
    for t in tickets:
        tl = timeline.get(t.pk, {"staff": [], "client": []})
        last_client = max([t.created_at] + tl["client"])
        last_staff = max(tl["staff"]) if tl["staff"] else None
        if t.status == Ticket.Status.PENDING:
            waiting_state, since = "on_client", last_staff or t.created_at
        elif last_staff and last_staff > last_client:
            waiting_state, since = "answered", last_staff
        else:
            waiting_state, since = "waiting", last_client
        seconds = int((now - since).total_seconds())
        rows.append({
            "id": t.pk,
            "number": t.ticket_number,
            "subject": t.subject,
            "department": t.department,
            "department_label": t.get_department_display(),
            "priority": t.priority,
            "status": t.status,
            "customer": t.customer_id,
            "customer_ref": t.customer.customer_id if t.customer_id else "",
            "customer_name": t.customer.full_name if t.customer_id else "",
            "assigned_to": t.assigned_to_id,
            "assigned_name": _person(t.assigned_to),
            "mine": t.assigned_to_id == user.pk,
            "created_at": t.created_at,
            "waiting_state": waiting_state,
            "waiting_since": since,
            "waiting_seconds": max(0, seconds),
            "waiting_long": waiting_state == "waiting" and seconds > WAITING_RED_HOURS * 3600,
            "line": lines.get(t.customer_id),
        })
    priority_rank = {"urgent": 0, "high": 1, "medium": 2, "low": 3}
    state_rank = {"waiting": 0, "answered": 1, "on_client": 2}
    rows.sort(key=lambda r: (state_rank[r["waiting_state"]], priority_rank.get(r["priority"], 9), -r["waiting_seconds"]))
    counts = {
        "all": len(rows),
        "mine": sum(1 for r in rows if r["mine"]),
        "unassigned": sum(1 for r in rows if not r["assigned_to"]),
        "urgent": sum(1 for r in rows if r["priority"] in ("urgent", "high")),
        "waiting_long": sum(1 for r in rows if r["waiting_long"]),
    }
    return {"counts": counts, "results": rows[:QUEUE_LIMIT]}


# ---------------------------------------------------------------------------
# Response to clients
# ---------------------------------------------------------------------------

def _period_start(period, now):
    local = timezone.localtime(now)
    if period == "today":
        return local.replace(hour=0, minute=0, second=0, microsecond=0)
    days = 7 if period == "week" else 30
    return local - datetime.timedelta(days=days)


def _buckets(period, now, cfg):
    """[(key, label)] in display order, and a function created_at -> key."""
    local = timezone.localtime(now)
    today = local.date()
    if period == "today":
        first = cfg.office_start.hour
        last = max(first, min(local.hour, (cfg.office_end.hour - 1) if cfg.office_end.minute == 0 else cfg.office_end.hour))
        keys = [(h, f"{h:02d}:00") for h in range(first, last + 1)]

        def key_of(dt):
            h = timezone.localtime(dt).hour
            return min(max(h, first), last)

        return keys, key_of
    if period == "week":
        keys = []
        for back in range(6, -1, -1):
            d = today - datetime.timedelta(days=back)
            keys.append((d, d.strftime("%a %d/%m")))

        def key_of(dt):
            return max(timezone.localtime(dt).date(), today - datetime.timedelta(days=6))

        return keys, key_of
    keys = []
    for wk in range(4, -1, -1):
        end = today - datetime.timedelta(days=7 * wk)
        start = end - datetime.timedelta(days=6)
        keys.append((wk, f"{start:%d/%m}–{end:%d/%m}"))

    def key_of(dt):
        return min(4, (today - timezone.localtime(dt).date()).days // 7)

    return keys, key_of


def response_stats(user, period, now, cfg, holidays):
    start = _period_start(period, now)
    target = cfg.reply_target_minutes
    base = _scoped_customer_tickets(user)

    created = list(base.filter(created_at__gte=start, created_at__lte=now))
    resolved = list(base.filter(resolved_at__gte=start, resolved_at__lte=now))
    waiting_open = list(base.filter(status=Ticket.Status.OPEN))
    timeline = _comment_timeline(list({t.pk for t in created + waiting_open}))

    client_raised = [t for t in created if _is_client_raised(t)]
    logged_by_staff = len(created) - len(client_raised)

    rows = []  # one per client-raised ticket: (ticket, minutes|None, verdict)
    for t in client_raised:
        reply = _first_staff_reply(t, timeline)
        if reply is not None:
            minutes = office_minutes(t.created_at, reply, cfg, holidays)
            rows.append((t, minutes, "within" if minutes <= target else "missed"))
        else:
            waited = office_minutes(t.created_at, now, cfg, holidays)
            rows.append((t, None, "missed" if waited > target else "undecided"))

    replied = [m for _, m, _ in rows if m is not None]
    within = sum(1 for _, _, v in rows if v == "within")
    decided = sum(1 for _, _, v in rows if v in ("within", "missed"))

    resolve_minutes = [office_minutes(t.created_at, t.resolved_at, cfg, holidays) for t in resolved]
    reopened = sum(1 for t in resolved if t.disqualified_from_kpi)

    # The oldest client-raised ticket nobody has answered yet (any age).
    oldest = None
    for t in waiting_open:
        if not _is_client_raised(t) or _first_staff_reply(t, timeline) is not None:
            continue
        if oldest is None or t.created_at < oldest.created_at:
            oldest = t
    oldest_row = None
    if oldest is not None:
        oldest_row = {
            "id": oldest.pk,
            "number": oldest.ticket_number,
            "customer_name": oldest.customer.full_name if oldest.customer_id else "",
            "priority": oldest.priority,
            "assigned_name": _person(oldest.assigned_to),
            "office_minutes": round(office_minutes(oldest.created_at, now, cfg, holidays), 1),
            "seconds": int((now - oldest.created_at).total_seconds()),
        }

    # Chart.
    keys, key_of = _buckets(period, now, cfg)
    per = {k: {"within": 0, "decided": 0} for k, _ in keys}
    for t, _, verdict in rows:
        if verdict == "undecided":
            continue
        k = key_of(t.created_at)
        if k not in per:
            continue
        per[k]["decided"] += 1
        if verdict == "within":
            per[k]["within"] += 1
    chart = []
    for k, label in keys:
        b = per[k]
        chart.append({
            "label": label,
            "within": b["within"],
            "decided": b["decided"],
            "pct": round(100 * b["within"] / b["decided"]) if b["decided"] else None,
        })

    # By staff member (by who the ticket is assigned to now).
    staff = defaultdict(lambda: {"tickets": 0, "replies": [], "within": 0, "decided": 0, "resolved": 0, "user": None})
    for t, minutes, verdict in rows:
        s = staff[t.assigned_to_id]
        s["user"] = t.assigned_to
        s["tickets"] += 1
        if minutes is not None:
            s["replies"].append(minutes)
        if verdict != "undecided":
            s["decided"] += 1
            if verdict == "within":
                s["within"] += 1
    for t in resolved:
        s = staff[t.assigned_to_id]
        s["user"] = t.assigned_to
        s["resolved"] += 1
    sees_all = user.role in ("admin", "management")
    staff_rows = []
    for uid, s in staff.items():
        if not sees_all and uid != user.pk:
            continue
        staff_rows.append({
            "user": uid,
            "name": _person(s["user"]) if uid else "Unassigned",
            "is_me": uid == user.pk,
            "tickets": s["tickets"],
            "first_reply_median": _median(s["replies"]),
            "within": s["within"],
            "decided": s["decided"],
            "within_pct": round(100 * s["within"] / s["decided"]) if s["decided"] else None,
            "resolved": s["resolved"],
        })
    staff_rows.sort(key=lambda r: (r["user"] is None, -r["tickets"], -r["resolved"], r["name"]))

    return {
        "period": period,
        "from": start,
        "to": now,
        "target_minutes": target,
        "tickets": len(created),
        "client_raised": len(client_raised),
        "logged_by_staff": logged_by_staff,
        "first_reply_median": _median(replied),
        "replied": len(replied),
        "within": within,
        "decided": decided,
        "within_pct": round(100 * within / decided) if decided else None,
        "resolved": len(resolved),
        "resolve_median": _median(resolve_minutes),
        "reopened": reopened,
        "oldest_waiting": oldest_row,
        "chart": chart,
        "staff": staff_rows,
        "staff_scope": "all" if sees_all else "own",
    }


# ---------------------------------------------------------------------------
# Recently offline + outages
# ---------------------------------------------------------------------------

def _network_points(usernames):
    """{username: (key, label, source)} -- where each line connects."""
    from billing.models import Service
    from radiusauth.models import RadAcct

    points = {}
    if not usernames:
        return points
    for row in (
        Service.objects.filter(radius_username__in=usernames)
        .exclude(status=Service.Status.TERMINATED)
        .values(
            "radius_username", "access_device_id", "access_device__name", "access_detail",
            "device_id", "device__name",
        )
    ):
        u = row["radius_username"]
        if u in points:
            continue
        if row["access_device_id"]:
            detail = (row["access_detail"] or "").strip()
            label = row["access_device__name"] + (f" · {detail}" if detail else "")
            points[u] = (("ap", row["access_device_id"], detail.lower()), label, "access point")
        elif row["device_id"]:
            points[u] = (("nas", row["device_id"]), f"{row['device__name']} (router)", "router")
    missing = [u for u in usernames if u not in points]
    if missing:
        seen = set()
        for row in (
            RadAcct.objects.filter(username__in=missing)
            .order_by("username", "-acctstarttime")
            .values("username", "nasipaddress")
        ):
            if row["username"] in seen:
                continue
            seen.add(row["username"])
            ip = row["nasipaddress"]
            if ip:
                points[row["username"]] = (("nasip", ip), f"Router {ip}", "router")
    return points


def find_outages(rows, points, min_clients, window_minutes):
    """Groups of at least `min_clients` offline clients on one network point
    whose last-seen times fall within `window_minutes` of each other."""
    groups = defaultdict(list)
    for r in rows:
        p = points.get(r["username"])
        if p is None:
            continue
        groups[p[0]].append((r["last_seen"], r))
    window = datetime.timedelta(minutes=window_minutes)
    out = []
    for key, items in groups.items():
        if len(items) < max(1, min_clients):
            continue
        items.sort(key=lambda x: x[0])
        best = (0, 0)  # (count, start index)
        j = 0
        for i in range(len(items)):
            while items[i][0] - items[j][0] > window:
                j += 1
            if i - j + 1 > best[0]:
                best = (i - j + 1, j)
        count, first = best
        if count < min_clients:
            continue
        chosen = items[first:first + count]
        label, source = points[chosen[0][1]["username"]][1:]
        out.append({
            "label": label,
            "source": source,
            "count": count,
            "first_down": chosen[0][0],
            "last_down": chosen[-1][0],
            "customers": sorted({r["customer"] for _, r in chosen}),
        })
    out.sort(key=lambda o: (-o["count"], o["label"]))
    return out


def offline_panel(user, cfg):
    from radiusauth.offline import recently_offline

    rows = recently_offline(list(_visible_customers(user)))
    points = _network_points([r["username"] for r in rows])
    outages = find_outages(rows, points, cfg.outage_min_clients, cfg.outage_window_minutes)
    with_ticket = set(
        Ticket.objects.filter(
            customer_id__in={r["customer"] for r in rows}, status__in=OPEN_STATUSES, is_internal=False
        ).values_list("customer_id", flat=True)
    )
    in_outage = {c for o in outages for c in o["customers"]}
    results = []
    for r in rows[:OFFLINE_LIMIT]:
        p = points.get(r["username"])
        results.append({
            "customer": r["customer"],
            "customer_ref": r["customer_ref"],
            "name": r["full_name"],
            "username": r["username"],
            "network_point": p[1] if p else "",
            "last_seen": r["last_seen"],
            "offline_seconds": r["offline_seconds"],
            "reason": r["terminate_reason"],
            "open_ticket": r["customer"] in with_ticket,
            "in_outage": r["customer"] in in_outage,
        })
    return {"count": len(rows), "outages": outages, "results": results}


# ---------------------------------------------------------------------------
# Cut-offs, waiting lists, visits
# ---------------------------------------------------------------------------

def cutoff_panel(user):
    from billing.models import SuspensionSettings
    from billing.recurring import billing_run_time, next_run_date, upcoming_blocks
    from notifications.models import EmailLog

    run_date = next_run_date()
    rows = upcoming_blocks(run_date, horizon_days=1, customers=_visible_customers(user))
    ids = [r["customer"] for r in rows]
    reminders = defaultdict(list)
    for cid, when in EmailLog.objects.filter(
        customer_id__in=ids, template_key="payment_reminder", status=EmailLog.Status.SENT
    ).values_list("customer_id", "created_at"):
        reminders[cid].append(timezone.localtime(when).date())
    results = []
    for r in rows:
        due = r["oldest_invoice_due"]
        sent = [d for d in reminders.get(r["customer"], []) if due is None or d >= due - datetime.timedelta(days=31)]
        results.append({
            "customer": r["customer"],
            "customer_ref": r["reference"],
            "name": r["name"],
            "balance": r["balance"],
            "oldest_invoice_due": due,
            "reminders_sent": len(sent),
            "extension_date": r["extension_date"],
        })
    return {
        "run_date": run_date,
        "run_time": billing_run_time(),
        "auto_suspend_enabled": SuspensionSettings.load().auto_suspend_enabled,
        "count": len(results),
        "extension_dates": sum(1 for r in results if r["extension_date"]),
        "results": results,
    }


def _item(key, label, sub, count, link="", items=None):
    return {"key": key, "label": label, "sub": sub, "count": count, "link": link, "items": items or []}


def waiting_lists(user, can):
    """The "Waiting on support" panel and the tiles that share its counts."""
    from billing.models import Service, ServicePause, SuspensionExtensionRequest
    from customers.models import Customer
    from customers.scoping import scope_by_customer

    today = timezone.localdate()
    week = today + datetime.timedelta(days=7)
    out = {}

    if can["customers"] or can["finance"]:
        ext = scope_by_customer(
            SuspensionExtensionRequest.objects.filter(status=SuspensionExtensionRequest.Status.PENDING)
            .select_related("customer", "requested_by").order_by("created_at"),
            user,
        )
        out["extensions"] = _item(
            "extensions", "Extension requests", "waiting for Management to approve", ext.count(), "",
            [{
                "customer": e.customer_id, "customer_ref": e.customer.customer_id, "name": e.customer.full_name,
                "detail": f"until {e.extend_until:%d/%m/%Y} · by {_person(e.requested_by) or 'unknown'}",
            } for e in ext[:LIST_LIMIT]],
        )

    if can["services"] or can["customers"]:
        pending = scope_by_customer(
            Service.objects.filter(status=Service.Status.PENDING).select_related("customer", "tariff").order_by("created_at"),
            user,
        )
        out["pending"] = _item(
            "pending", "Pending activations", "line added, not switched on yet", pending.count(),
            "/admin/services?status=pending",
            [{
                "customer": s.customer_id, "customer_ref": s.customer.customer_id, "name": s.customer.full_name,
                "detail": " · ".join(x for x in [
                    s.tariff.name if s.tariff_id else "",
                    s.radius_username or "no login",
                    f"switches on {s.activate_on:%d/%m}" if s.activate_on else "",
                ] if x),
            } for s in pending[:LIST_LIMIT]],
        )
        paused = scope_by_customer(Service.objects.filter(status=Service.Status.PAUSED), user)
        ending = scope_by_customer(
            ServicePause.objects.filter(
                status=ServicePause.Status.ACTIVE, resume_on__isnull=False, resume_on__gte=today, resume_on__lte=week,
            ).select_related("customer", "service__tariff").order_by("resume_on"),
            user,
        )
        out["paused"] = {"count": paused.count(), "resuming_this_week": ending.count()}
        out["pauses_ending"] = _item(
            "pauses_ending", "Pauses ending this week", "the line comes back on by itself", ending.count(),
            "/admin/services?status=paused",
            [{
                "customer": p.customer_id, "customer_ref": p.customer.customer_id, "name": p.customer.full_name,
                "detail": f"back on {p.resume_on:%a %d/%m}" + (f" · {p.service.tariff.name}" if p.service.tariff_id else ""),
            } for p in ending[:LIST_LIMIT]],
        )

    # Return visits a technician asked for (Conecto "return booking").
    internal = scope_by_customer(
        Ticket.objects.filter(
            is_internal=True, status__in=OPEN_STATUSES, conecto_internal__category="return_booking",
        ).select_related("conecto_internal__job__customer").order_by("created_at"),
        user, path="conecto_internal__job__customer", allow_null=True,
    )
    out["return_visits"] = _item(
        "return_visits", "Return visits to book", "a technician asked for a return visit", internal.count(),
        "/admin/tickets?status=open",
        [{
            "ticket": t.pk, "number": t.ticket_number,
            "name": (t.conecto_internal.job.customer.full_name
                     if getattr(t, "conecto_internal", None) and t.conecto_internal.job_id and t.conecto_internal.job.customer_id
                     else t.subject),
            "detail": t.subject,
        } for t in internal[:LIST_LIMIT]],
    )

    if can["customers"]:
        no_email = _visible_customers(user).filter(status__in=Customer.LIVE_STATUSES).filter(
            Q(email="") | Q(email__isnull=True)
        ).filter(Q(billing_email="") | Q(billing_email__isnull=True)).order_by("full_name")
        out["no_email"] = _item(
            "no_email", "Live clients with no email", "they get no invoices or reminders", no_email.count(), "",
            [{"customer": c.pk, "customer_ref": c.customer_id, "name": c.full_name, "detail": c.phone or "no phone either"}
             for c in no_email[:LIST_LIMIT]],
        )
    return out


def visits_today(user):
    from customers.scoping import scope_by_customer
    from scheduling.models import Job, JobType

    today = timezone.localdate()
    jobs = scope_by_customer(
        Job.objects.filter(deleted_at__isnull=True, start__date=today)
        .exclude(status=Job.Status.CANCELLED)
        .select_related("customer")
        .prefetch_related("assigned_technicians__assigned_vehicles")
        .order_by("start"),
        user,
    )
    names = dict(JobType.objects.values_list("key", "name"))
    out = []
    for j in jobs[:30]:
        techs = list(j.assigned_technicians.all())
        vehicles = sorted({v.registration_number for t in techs for v in t.assigned_vehicles.all() if v.is_active})
        out.append({
            "id": j.pk,
            "start": j.start,
            "title": j.title,
            "job_type": j.job_type,
            "job_type_label": names.get(j.job_type, j.job_type.replace("_", " ").title()),
            "status": j.status,
            "status_label": j.get_status_display(),
            "urgent": j.urgent,
            "customer": j.customer_id,
            "customer_name": j.customer.full_name if j.customer_id else "",
            "technicians": [_person(t) for t in techs],
            "vehicles": vehicles,
        })
    return out


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------

def dashboard(user, period="week", now=None):
    from billing.business_days import holiday_set
    from billing.recurring import billing_run_time, next_run_date

    now = now or timezone.now()
    if period not in PERIODS:
        period = "week"
    cfg = SupportSettings.load()
    can = {
        "customers": user_can_access_section(user, "customers"),
        "finance": user_can_access_section(user, "finance"),
        "services": user_can_access_section(user, "services"),
        "scheduling": user_can_access_section(user, "scheduling"),
    }
    # Every holiday on file, once: a resolved ticket can have been opened long
    # before the period, and the table holds a few dozen rows a year.
    holidays = holiday_set(end=timezone.localdate(now))

    queue = ticket_queue(user, now)
    stats = response_stats(user, period, now, cfg, holidays)
    offline = offline_panel(user, cfg) if can["customers"] else None
    cutoffs = cutoff_panel(user) if (can["customers"] or can["finance"]) else None
    lists = waiting_lists(user, can)
    visits = visits_today(user) if can["scheduling"] else None

    paused = lists.pop("paused", None)
    waiting = [lists[k] for k in ("extensions", "pending", "return_visits", "no_email", "pauses_ending") if k in lists]
    tiles = {
        "open_tickets": {"count": queue["counts"]["all"], "waiting_long": queue["counts"]["waiting_long"]},
        "offline": None if offline is None else {
            "count": offline["count"],
            "in_outage": len({c for o in offline["outages"] for c in o["customers"]}),
        },
        "cutoffs": None if cutoffs is None else {"count": cutoffs["count"], "extension_dates": cutoffs["extension_dates"]},
        "extensions": lists["extensions"]["count"] if "extensions" in lists else None,
        "pending": lists["pending"]["count"] if "pending" in lists else None,
        "paused": paused,
    }
    return {
        "generated_at": now,
        "today": timezone.localdate(now),
        "next_run_date": next_run_date(),
        "run_time": billing_run_time(),
        "settings": {
            "reply_target_minutes": cfg.reply_target_minutes,
            "office_start": cfg.office_start.strftime("%H:%M"),
            "office_end": cfg.office_end.strftime("%H:%M"),
            "office_days": cfg.office_days,
            "outage_min_clients": cfg.outage_min_clients,
            "outage_window_minutes": cfg.outage_window_minutes,
        },
        "access": can,
        "tiles": tiles,
        "queue": queue,
        "response": stats,
        "offline": offline,
        "cutoffs": cutoffs,
        "waiting": waiting,
        "visits": visits,
    }
