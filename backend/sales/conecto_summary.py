"""The sales dashboard figures Conecto's app pulls (7 Oct 2026).

POST /api/conecto/sales/summary/ with {staff_code or null, date_from,
date_to}; the response shape is Conecto's, see summary() below.

Definitions, so both sides agree:

* "in the range" = the date (SA time) falls between date_from and date_to,
  both inclusive.
* new_leads: created in the range.
* open_*, follow_ups_due, open_by_stage, follow_ups: as things stand NOW.
* quotes_sent / quotes_value: quote sent in the range; value = the lead's
  monthly value.
* accepted / lost: closed in the range. Values are monthly, excl. VAT.
* conversion_rate = accepted / (accepted + lost) x 100 over leads closed in
  the range; null when none closed.
* by_source / by_reseller / by_plan: leads created in the range, and how
  many of those are accepted.
"""
import datetime
from decimal import Decimal

from django.utils import timezone

from .models import Lead


def _money(value):
    return float(Decimal(value or 0).quantize(Decimal("0.01")))


def _name(user):
    if user is None:
        return ""
    return user.get_full_name() or user.username


def _lead_name(lead):
    return lead.company_name or lead.full_name


def _in_range(moment, date_from, date_to):
    if moment is None:
        return False
    day = timezone.localtime(moment).date() if isinstance(moment, datetime.datetime) else moment
    return date_from <= day <= date_to


def _rate(accepted, lost):
    closed = accepted + lost
    return round(accepted * 100.0 / closed, 1) if closed else None


def _totals(leads, date_from, date_to, today):
    new = [l for l in leads if _in_range(l.created_at, date_from, date_to)]
    open_ = [l for l in leads if l.status in Lead.OPEN_STATUSES]
    quoted = [l for l in leads if _in_range(l.quote_sent_at, date_from, date_to)]
    accepted = [l for l in leads if l.status == Lead.Status.WON and _in_range(l.closed_at, date_from, date_to)]
    lost = [l for l in leads if l.status == Lead.Status.LOST and _in_range(l.closed_at, date_from, date_to)]
    due = [l for l in open_ if l.next_follow_up and l.next_follow_up <= today]
    return {
        "new_leads": len(new),
        "open_leads": len(open_),
        "open_value": _money(sum((l.value for l in open_), Decimal(0))),
        "follow_ups_due": len(due),
        "quotes_sent": len(quoted),
        "quotes_value": _money(sum((l.value for l in quoted), Decimal(0))),
        "accepted": len(accepted),
        "accepted_value": _money(sum((l.value for l in accepted), Decimal(0))),
        "lost": len(lost),
        "conversion_rate": _rate(len(accepted), len(lost)),
    }, new, open_, lost, due


def _grouped(new_leads, key):
    groups = {}
    for lead in new_leads:
        label = key(lead)
        row = groups.setdefault(label, {"leads": 0, "accepted": 0})
        row["leads"] += 1
        if lead.status == Lead.Status.WON:
            row["accepted"] += 1
    return sorted(groups.items(), key=lambda kv: (-kv[1]["leads"], kv[0]))


def summary(user, date_from, date_to, now=None):
    """`user` = one salesperson (their own leads), or None for the team."""
    now = now or timezone.now()
    today = timezone.localtime(now).date()
    leads = list(
        Lead.objects.select_related("interested_tariff", "partner", "assigned_to")
        .filter(**({"assigned_to": user} if user is not None else {}))
    )
    totals, new, open_, lost, due = _totals(leads, date_from, date_to, today)

    labels = dict(Lead.Status.choices)
    open_by_stage = []
    for status in Lead.OPEN_STATUSES:
        rows = [l for l in open_ if l.status == status]
        open_by_stage.append({
            "stage": status, "label": labels[status], "count": len(rows),
            "value": _money(sum((l.value for l in rows), Decimal(0))),
        })

    follow_ups = [
        {
            "lead_id": l.pk,
            "lead_name": _lead_name(l),
            "due_date": l.next_follow_up.isoformat(),
            "stage": labels.get(l.status, l.status),
            "plan": l.interested_tariff.name if l.interested_tariff_id else "",
            "owner_name": _name(l.assigned_to),
        }
        for l in sorted(due, key=lambda l: (l.next_follow_up, l.pk))[:50]
    ]

    reasons = dict(Lead.LostReason.choices)
    by_reason, by_stage = {}, {}
    for l in lost:
        by_reason[l.lost_reason] = by_reason.get(l.lost_reason, 0) + 1
        stage = l.lost_at_stage or Lead.Status.NEW
        by_stage[stage] = by_stage.get(stage, 0) + 1

    result = {
        "as_of": timezone.localtime(now).isoformat(),
        "totals": totals,
        "open_by_stage": open_by_stage,
        "follow_ups": follow_ups,
        "lost_by_reason": [
            {"reason": reasons.get(k, k), "key": k, "count": v}
            for k, v in sorted(by_reason.items(), key=lambda kv: -kv[1])
        ],
        "lost_by_stage": [
            {"stage": labels.get(k, k), "key": k, "count": v}
            for k, v in sorted(by_stage.items(), key=lambda kv: -kv[1])
        ],
        "by_source": [
            {"source": label, **row}
            for label, row in _grouped(new, lambda l: l.get_source_display())
        ],
        "by_reseller": [
            {"reseller": label, **row}
            for label, row in _grouped(new, lambda l: l.partner.name if l.partner_id else "Direct")
        ],
        "by_plan": [
            {"plan": label, **row}
            for label, row in _grouped(new, lambda l: l.interested_tariff.name if l.interested_tariff_id else "Not chosen")
        ],
    }

    if user is None:
        people = {}
        for lead in leads:
            if lead.assigned_to_id:
                people.setdefault(lead.assigned_to_id, (lead.assigned_to, []))[1].append(lead)
        by_person = []
        for person, own in people.values():
            t = _totals(own, date_from, date_to, today)[0]
            by_person.append({
                "staff_code": person.staff_code or "",
                "name": _name(person),
                "new_leads": t["new_leads"],
                "open_leads": t["open_leads"],
                "open_value": t["open_value"],
                "quotes_sent": t["quotes_sent"],
                "accepted": t["accepted"],
                "accepted_value": t["accepted_value"],
                "lost": t["lost"],
                "conversion_rate": t["conversion_rate"],
            })
        result["by_person"] = sorted(by_person, key=lambda r: (-r["accepted_value"], r["name"]))
    return result
