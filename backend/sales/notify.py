"""Sales notifications to the Conecto app (salesEvent, 7 Oct 2026).

Three events, each sent to the lead's owner (Conecto also tells admins
about lead_accepted): lead_assigned, scan_result, lead_accepted. Sent after
the change is committed, and only when the owner has a staff code -- the
app finds the person by it.
"""
from django.db import transaction


def _payload(event, lead, **extra):
    owner = lead.assigned_to if lead.assigned_to_id else None
    code = getattr(owner, "staff_code", "") if owner is not None else ""
    if not code:
        return None
    body = {
        "event": event,
        "staff_code": code,
        "lead_id": lead.pk,
        "lead_name": lead.company_name or lead.full_name,
    }
    body.update({k: v for k, v in extra.items() if v})
    return body


def send(event, lead, **extra):
    from conecto import client

    body = _payload(event, lead, **extra)
    if body is None:
        return
    transaction.on_commit(
        lambda: client.call("salesEvent", body, subject_kind="lead", subject_ref=str(body["lead_id"]))
    )


def lead_assigned(lead, by_user=None):
    """Someone else gave this person the lead (assigning yourself is not news)."""
    if not lead.assigned_to_id or (by_user is not None and lead.assigned_to_id == by_user.pk):
        return
    send("lead_assigned", lead)


def scan_result(lead):
    send("scan_result", lead, result=lead.coverage_result)


def lead_accepted(lead):
    send("lead_accepted", lead, client_code=getattr(lead.customer, "customer_id", "") if lead.customer_id else "")
