"""The sales screens of the Conecto app (7 Oct 2026): lead list, lead detail
with its timeline, and three actions (log a note, set the follow-up, mark
lost). Conecto calls these with the salesperson's staff code; the rules
below are the same ones the web platform applies.

* Only staff with the Sales section can use them (admins always can).
* "mine" = leads they own. "team" = every lead they may see (reseller
  restrictions apply) and is for admins and management only.
"""
import datetime

from django.contrib.auth import get_user_model
from django.core import signing
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_date

from .models import Lead, LeadNote
from .serializers import LeadNoteSerializer, LeadSerializer

PAGE_SIZE = 25
TEAM_ROLES = ("admin", "management")
NOTE_KINDS = ("note", "call", "email", "meeting")
QUOTE_LINK_SECONDS = 900
_QUOTE_SALT = "conecto-lead-quote"


class SalesApiError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def staff_user(staff_code):
    from accounts.permissions import user_can_access_section

    code = str(staff_code or "").strip()
    if not code:
        raise SalesApiError("staff_code is required.")
    user = get_user_model().objects.filter(staff_code__iexact=code).first()
    if user is None:
        raise SalesApiError(f"No staff member with code {code!r}.", status=404)
    if not user.is_active or not user.is_staff_member or not user_can_access_section(user, "sales"):
        raise SalesApiError("This staff member has no access to Sales.", status=403)
    return user


def can_see_team(user):
    return user.role in TEAM_ROLES


def visible_leads(user, scope="mine"):
    from .views import scope_to_partners

    qs = Lead.objects.select_related(
        "partner", "assigned_to", "interested_tariff", "customer",
        "coverage_partner", "coverage_checked_by", "scan_job", "install_job",
    )
    if scope == "team":
        if not can_see_team(user):
            raise SalesApiError("Only admins and management can see the whole team's leads.", status=403)
        return scope_to_partners(qs, user)
    return qs.filter(assigned_to=user)


def get_lead(user, lead_id):
    try:
        pk = int(lead_id)
    except (TypeError, ValueError):
        raise SalesApiError("lead_id must be a number.")
    scope = "team" if can_see_team(user) else "mine"
    lead = visible_leads(user, scope).filter(pk=pk).first()
    if lead is None:
        raise SalesApiError("No such lead, or it isn't yours.", status=404)
    return lead


def _owner(lead):
    user = lead.assigned_to
    if user is None:
        return "", ""
    return (user.get_full_name() or user.username), (user.staff_code or "")


def list_row(lead):
    owner, owner_code = _owner(lead)
    return {
        "id": lead.pk,
        "full_name": lead.full_name,
        "company_name": lead.company_name,
        "city": lead.city,
        "status": lead.status,
        "status_display": lead.get_status_display(),
        "value": float(lead.value or 0),
        "tariff_name": lead.interested_tariff.name if lead.interested_tariff_id else "",
        "coverage_method": lead.coverage_method,
        "coverage_result": lead.coverage_result,
        "next_action": _next_action(lead),
        "next_follow_up": lead.next_follow_up.isoformat() if lead.next_follow_up else None,
        "follow_up_is_due": lead.follow_up_is_due,
        "owner": owner,
        "owner_staff_code": owner_code,
    }


def _next_action(lead):
    from .workflow import next_action

    return next_action(lead)


def list_leads(user, data):
    scope = str(data.get("scope") or "mine").lower()
    if scope not in ("mine", "team"):
        raise SalesApiError('scope must be "mine" or "team".')
    view = str(data.get("view") or "open").lower()
    if view not in ("open", "due", "all"):
        raise SalesApiError('view must be "open", "due" or "all".')
    qs = visible_leads(user, scope)
    today = timezone.localdate()
    if view in ("open", "due"):
        qs = qs.filter(status__in=Lead.OPEN_STATUSES)
    if view == "due":
        qs = qs.filter(next_follow_up__isnull=False, next_follow_up__lte=today).order_by("next_follow_up", "pk")
    else:
        qs = qs.order_by("-created_at")
    stage = str(data.get("stage") or "").strip()
    if stage:
        if stage not in Lead.Status.values:
            raise SalesApiError(f"Unknown stage {stage!r}.")
        qs = qs.filter(status=stage)
    search = str(data.get("search") or "").strip()
    if search:
        qs = qs.filter(
            Q(full_name__icontains=search) | Q(company_name__icontains=search) | Q(phone__icontains=search)
            | Q(email__icontains=search) | Q(city__icontains=search) | Q(address__icontains=search)
        )
    try:
        page = max(1, int(data.get("page") or 1))
    except (TypeError, ValueError):
        raise SalesApiError("page must be a number.")
    count = qs.count()
    rows = list(qs[(page - 1) * PAGE_SIZE: page * PAGE_SIZE])
    return {
        "count": count,
        "page": page,
        "page_size": PAGE_SIZE,
        "has_more": page * PAGE_SIZE < count,
        "results": [list_row(l) for l in rows],
    }


def quote_pdf_link(request, lead):
    if not lead.quote_sent_at:
        return None, None
    from conecto.views import _document_base

    token = signing.TimestampSigner(salt=_QUOTE_SALT).sign(str(lead.pk))
    expires = timezone.now() + datetime.timedelta(seconds=QUOTE_LINK_SECONDS)
    return f"{_document_base(request)}/api/public/lead-quote/{token}/", expires.isoformat()


def lead_from_quote_token(token):
    try:
        pk = signing.TimestampSigner(salt=_QUOTE_SALT).unsign(token, max_age=QUOTE_LINK_SECONDS)
    except signing.BadSignature:
        return None
    return Lead.objects.filter(pk=pk).first()


def lead_detail(request, lead):
    data = dict(LeadSerializer(lead).data)
    owner, owner_code = _owner(lead)
    data["owner"] = owner
    data["owner_staff_code"] = owner_code
    data["timeline"] = [
        {k: v for k, v in note.items() if k in ("id", "kind", "kind_display", "body", "author_name", "created_at")}
        for note in LeadNoteSerializer(lead.lead_notes.select_related("author").order_by("-created_at"), many=True).data
    ]
    url, expires = quote_pdf_link(request, lead)
    data["quote_pdf_url"] = url
    data["quote_pdf_expires_at"] = expires
    return data


def apply_action(user, lead, data):
    from .workflow import FlowError, reject

    action = str(data.get("action") or "").strip().lower()
    if action == "note":
        kind = str(data.get("kind") or "note").lower()
        if kind not in NOTE_KINDS:
            raise SalesApiError(f'kind must be one of {", ".join(NOTE_KINDS)}.')
        body = str(data.get("body") or "").strip()
        if not body:
            raise SalesApiError("body is required.")
        follow = data.get("next_follow_up")
        when = None
        if follow:
            when = parse_date(str(follow))
            if when is None:
                raise SalesApiError("next_follow_up must be YYYY-MM-DD.")
            if not lead.is_open:
                raise SalesApiError("This lead is closed, so it can't have a follow-up.")
        LeadNote.objects.create(lead=lead, author=user, kind=kind, body=body)
        if when is not None:
            lead.next_follow_up = when
            lead.save(update_fields=["next_follow_up", "updated_at"])
    elif action == "follow_up":
        if "next_follow_up" not in data:
            raise SalesApiError("next_follow_up is required (a date, or null to clear it).")
        if not lead.is_open:
            raise SalesApiError("This lead is closed, so it can't have a follow-up.")
        follow = data.get("next_follow_up")
        when = None
        if follow:
            when = parse_date(str(follow))
            if when is None:
                raise SalesApiError("next_follow_up must be YYYY-MM-DD, or null.")
        lead.next_follow_up = when
        lead.save(update_fields=["next_follow_up", "updated_at"])
        LeadNote.objects.create(
            lead=lead, author=user, kind=LeadNote.Kind.SYSTEM,
            body=f"Follow-up set to {when:%d %b %Y}." if when else "Follow-up cleared.",
        )
    elif action == "lost":
        reason = str(data.get("lost_reason") or "").strip()
        if reason not in Lead.LostReason.values:
            raise SalesApiError("lost_reason must be one of: " + ", ".join(Lead.LostReason.values) + ".")
        try:
            reject(lead, user, reason=reason, note=str(data.get("note") or "").strip())
        except FlowError as exc:
            raise SalesApiError(str(exc))
    else:
        raise SalesApiError('action must be "note", "follow_up" or "lost".')
    lead.refresh_from_db()
    return lead
