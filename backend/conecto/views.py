"""The webhooks Conecto calls on us.

    POST /api/webhooks/conecto/ticket-created/
    POST /api/webhooks/conecto/ticket-closed/
    POST /api/webhooks/conecto/client-registered/
    POST /api/webhooks/conecto/job-status-changed/
    POST /api/webhooks/conecto/job-checklist-completed/
    POST /api/webhooks/conecto/technician-position-updated/
    POST /api/webhooks/conecto/internal-ticket-created/
    POST /api/webhooks/conecto/internal-ticket-replied/
    POST /api/webhooks/conecto/ticket-note-added/

These are the only unauthenticated-by-JWT endpoints in the platform
apart from the health check and the two signed public customer pages, so
the rules they follow are deliberate:

BEARER TOKEN, COMPARED IN CONSTANT TIME. `==` on a secret leaks its
length and, over enough requests, its contents through timing. That is a
theoretical attack on a fast local comparison and a cheap one to close,
so secrets.compare_digest. A missing or wrong token is 401 and nothing
else -- no hint about which.

NO KEY CONFIGURED MEANS CLOSED, NOT OPEN. If CONECTO_WEBHOOK_TOKEN is
empty these endpoints refuse everything. The opposite default would turn
any deployment that had not finished configuring the integration into an
open ticket-creation endpoint.

THROTTLED. Anyone on the internet can reach these, and a wrong token
must not be cheap to retry in bulk -- the same reasoning as the public
usage page's throttle.

UNKNOWN CLIENT CODES ARE REFUSED, NOT INVENTED. A ticket for a client
code we do not have is a 404 with a plain message. Creating a customer
from a webhook would let anybody who learns the token manufacture
customer records, and a typo would silently produce a second, empty
account beside the real one.

IDEMPOTENT. Conecto retries nothing, but proxies and operators do. Every
receiver keys off an id it can recognise and returns the existing record
rather than creating a second one.
"""
from datetime import timedelta

from rest_framework import permissions
import logging
import secrets
import uuid

from django.db import models

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import exceptions, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView

from accounts.permissions import IsStaffMember, section_permission
from customers.models import Customer
from tickets.models import Ticket
from tickets.models import TicketComment

from .mapping import (
    department_from_category,
    priority_from_conecto,
    status_from_conecto,
)
from .models import ConectoLink, ConectoTicket
from .signals import suppressed

logger = logging.getLogger(__name__)


class BadToken(exceptions.APIException):
    """401, and a plain one.

    Not DRF's NotAuthenticated/AuthenticationFailed: those are downgraded
    to 403 when the view has no authenticators to name in a
    WWW-Authenticate header, and these views deliberately have none. A
    caller holding the wrong secret should be told it is the secret.
    """

    status_code = status.HTTP_401_UNAUTHORIZED
    default_detail = "Invalid or missing bearer token."
    default_code = "invalid_token"


def _webhook_token():
    """The token Conecto must present.

    Falls back to the outbound API key so a single shared secret works,
    which is how the integration was specified -- but can be set on its
    own so the two directions can be rotated independently later.
    """
    return getattr(settings, "CONECTO_WEBHOOK_TOKEN", "") or getattr(
        settings, "CONECTO_API_KEY", ""
    )


class WebhookThrottle(SimpleRateThrottle):
    """Per-IP, with its rate read from a plain setting.

    Deliberately NOT ScopedRateThrottle: that reads
    REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"], and a scope missing from
    that dict makes DRF raise ImproperlyConfigured at request time -- so
    a deployment that took this app but not the settings edit would 500
    on every webhook instead of merely being unthrottled. One setting
    with a default cannot be half-applied.
    """

    scope = "conecto_webhook"

    def get_rate(self):
        return getattr(settings, "CONECTO_WEBHOOK_RATE", "120/min")

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


class ConectoWebhook(APIView):
    """Shared plumbing: no session/JWT auth, bearer token, throttle."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [WebhookThrottle]

    def dispatch(self, request, *args, **kwargs):
        # Changes a webhook makes (a job completed or returned from the
        # app...) read "Conecto app" in the audit trail and the Scheduling
        # changelog instead of a blank actor (8 Oct 2026).
        from audit.context import acting_as_system

        with acting_as_system("Conecto app"):
            return super().dispatch(request, *args, **kwargs)

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        expected = _webhook_token()
        presented = ""
        header = request.META.get("HTTP_AUTHORIZATION", "")
        if header.lower().startswith("bearer "):
            presented = header[7:].strip()

        if not expected or not presented or not secrets.compare_digest(presented, expected):
            logger.warning(
                "Conecto webhook rejected at %s (token %s)",
                request.path,
                "missing" if not presented else "wrong",
            )
            raise BadToken()

    # --- helpers ---------------------------------------------------------

    @staticmethod
    def _first(data, *keys):
        """Conecto's spec and its examples disagree on a couple of field
        names (client_code vs clientCode, and so on). Accepting both is
        one line here and saves a silent 404 in production."""
        for key in keys:
            value = data.get(key)
            if value not in (None, ""):
                return value
        return ""

    def _customer(self, client_code):
        return Customer.objects.filter(customer_id__iexact=str(client_code).strip()).first()


class TicketCreatedWebhook(ConectoWebhook):
    """A subscriber raised a ticket in the app."""

    def post(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        conecto_id = str(self._first(data, "ticket_id", "ticketId", "conecto_ticket_id") or "")
        client_code = self._first(data, "client_code", "clientCode")

        if not conecto_id:
            return Response(
                {"detail": "ticket_id is required."}, status=status.HTTP_400_BAD_REQUEST
            )

        existing = ConectoTicket.objects.select_related("ticket").filter(
            conecto_ticket_id=conecto_id
        ).first()
        if existing:
            # A replay. Answer as though it worked, because from Conecto's
            # side it did, and a 400 here would just be logged and dropped.
            return Response(
                {"ticket_number": existing.ticket.ticket_number, "duplicate": True}
            )

        customer = self._customer(client_code)
        if customer is None:
            logger.warning("Conecto ticket for unknown client code %r", client_code)
            return Response(
                {"detail": f"No customer with client code {client_code!r}."},
                status=status.HTTP_404_NOT_FOUND,
            )

        subject = (self._first(data, "subject", "title") or "").strip()[:255]
        if not subject:
            subject = "Ticket from the Conecto app"

        priority = priority_from_conecto(self._first(data, "priority")) or Ticket.Priority.MEDIUM
        department = department_from_category(self._first(data, "category"))

        with transaction.atomic():
            # suppressed(): this ticket came FROM Conecto. Pushing it back
            # would create a second copy of it there.
            with suppressed():
                ticket = Ticket.objects.create(
                    customer=customer,
                    subject=subject,
                    description=self._first(data, "description", "body") or "",
                    department=department,
                    priority=priority,
                    status=Ticket.Status.OPEN,
                )
                ConectoTicket.objects.create(
                    ticket=ticket,
                    conecto_ticket_id=conecto_id,
                    origin=ConectoTicket.Origin.CONECTO,
                )

        return Response(
            {"ticket_number": ticket.ticket_number, "vexar_ticket_id": ticket.ticket_number},
            status=status.HTTP_201_CREATED,
        )


class TicketClosedWebhook(ConectoWebhook):
    """A subscriber closed their ticket in the app.

    Closed only -- this is not a general status endpoint. Letting the app
    move a ticket to any state would let a subscriber reopen something an
    agent had finished, or set a priority nobody agreed to.
    """

    def post(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        conecto_id = str(self._first(data, "ticket_id", "ticketId", "conecto_ticket_id") or "")
        vexar_ref = str(self._first(data, "vexar_ticket_id", "ticket_number") or "")

        link = None
        if conecto_id:
            link = ConectoTicket.objects.select_related("ticket").filter(
                conecto_ticket_id=conecto_id
            ).first()
        ticket = link.ticket if link else None
        if ticket is None and vexar_ref:
            # Our own number is the other handle Conecto holds, and it is
            # the one it has for tickets WE pushed.
            ticket = Ticket.objects.filter(ticket_number=vexar_ref).first()

        if ticket is None:
            return Response(
                {"detail": "Unknown ticket."}, status=status.HTTP_404_NOT_FOUND
            )

        new_status = status_from_conecto(self._first(data, "status")) or Ticket.Status.CLOSED
        if new_status != Ticket.Status.CLOSED:
            # The endpoint is named for one transition; anything else
            # arriving here is a mistake on their side, not an instruction.
            return Response(
                {"detail": "This endpoint only closes tickets."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if ticket.status == Ticket.Status.CLOSED:
            return Response({"ticket_number": ticket.ticket_number, "already_closed": True})

        with suppressed():
            ticket.status = Ticket.Status.CLOSED
            ticket.save(update_fields=["status", "updated_at"])

        return Response({"ticket_number": ticket.ticket_number, "status": "closed"})


class TicketRepliedWebhook(ConectoWebhook):
    """A subscriber replied to their ticket in the app.

    The counterpart to client.add_ticket_reply. Without this the
    integration is worse than nothing: staff answer into a thread they
    believe is two-way, the customer answers back, and nobody at Skybre
    ever sees it.

    The reply is stored as an ordinary ticket comment with
    external_author set to the name Conecto gives us. That field is what
    distinguishes it from a staff comment on screen -- author stays null
    because the customer is not a user of this system, and a comment with
    no name against it would read as if the platform had written it.

    is_internal is False and is never taken from the payload. Whether
    something is an internal note is our decision about our own staff's
    writing; an inbound field that could set it would let the app mark a
    customer's own message hidden from the customer.
    """

    def post(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        conecto_id = str(self._first(data, "ticket_id", "ticketId", "conecto_ticket_id") or "")
        vexar_ref = str(self._first(data, "vexar_ticket_id", "ticket_number") or "")
        message = str(self._first(data, "message", "text", "body", "reply") or "").strip()
        author = str(self._first(data, "author_name", "authorName") or "").strip()[:150]
        # Skybre staff answering in the Conecto app arrive here too, with
        # author_type "staff" (Conecto, 7 Oct 2026). Labelled as staff so
        # nobody here reads them as the customer talking.
        author_type = str(self._first(data, "author_type", "authorType") or "").strip().lower()
        if author_type in ("staff", "admin"):
            role = str(self._first(data, "author_role", "authorRole") or "").strip()
            label = author or str(self._first(data, "author_nickname", "authorNickname") or "").strip()
            author = f"{label or 'Staff'} (Skybre {role or 'staff'}, via Conecto)"[:150]

        if not message:
            return Response(
                {"detail": "message is required."}, status=status.HTTP_400_BAD_REQUEST
            )

        link = None
        if conecto_id:
            link = ConectoTicket.objects.select_related("ticket").filter(
                conecto_ticket_id=conecto_id
            ).first()
        ticket = link.ticket if link else None
        if ticket is None and vexar_ref:
            # The other handle they hold -- our own number, which is what
            # they have for tickets we pushed to them.
            ticket = Ticket.objects.filter(ticket_number=vexar_ref).first()

        if ticket is None:
            return Response(
                {"detail": "Unknown ticket."}, status=status.HTTP_404_NOT_FOUND
            )

        # A replay answers as though it worked. Conecto logs and drops a
        # failure rather than retrying, so a 400 here would achieve
        # nothing except losing the reply -- and the same message twice in
        # a row from the same person is far more likely to be a duplicate
        # delivery than someone repeating themselves verbatim.
        last = ticket.comments.order_by("-created_at").first()
        if last is not None and last.message == message and last.external_author == (author or "Customer"):
            return Response({"ticket_number": ticket.ticket_number, "duplicate": True})

        with suppressed():
            TicketComment.objects.create(
                ticket=ticket,
                author=None,
                message=message,
                is_internal=False,
                # Named even when they send us nothing, because a reply
                # with no author beside it reads as though Skybre wrote
                # it, and staff would answer accordingly.
                external_author=author or "Customer",
            )

        # Status is deliberately untouched. A customer replying to a
        # closed ticket does not reopen it here: TicketClosedWebhook
        # exists precisely so the app cannot move a ticket to any state it
        # likes, and reopening through a side door would undo that. Worth
        # revisiting as a product decision -- a reply on a closed ticket
        # should probably surface somewhere -- but not as a silent
        # side-effect of this endpoint.
        return Response(
            {"ticket_number": ticket.ticket_number}, status=status.HTTP_201_CREATED
        )


class ClientRegisteredWebhook(ConectoWebhook):
    """A subscriber finished registering in the app."""

    def post(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        client_code = self._first(data, "client_code", "clientCode")
        firebase_uid = str(self._first(data, "firebase_uid", "firebaseUid", "uid") or "")

        if not firebase_uid:
            return Response(
                {"detail": "firebase_uid is required."}, status=status.HTTP_400_BAD_REQUEST
            )

        customer = self._customer(client_code)
        if customer is None:
            return Response(
                {"detail": f"No customer with client code {client_code!r}."},
                status=status.HTTP_404_NOT_FOUND,
            )

        email = self._first(data, "email") or ""

        # update_or_create on the CUSTOMER, not the uid: a customer who
        # reinstalls the app and registers again gets a new Firebase uid,
        # and the row should follow them rather than accumulating.
        link, created = ConectoLink.objects.update_or_create(
            customer=customer,
            defaults={"firebase_uid": firebase_uid, "email": email},
        )

        return Response(
            {"customer_id": customer.customer_id, "created": created},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


# --- job dispatch ---------------------------------------------------------


#: What the app sends when a technician presses Return instead of Complete.
#: Several spellings accepted until Conecto confirms theirs.
RETURN_STATUSES = ("returned", "return", "returned_to_pool", "return_to_pool")


class JobStatusChangedWebhook(ConectoWebhook):
    """A technician moved a job along: en route, on site, done.

    EVERY update is recorded as a ConectoJobEvent, whether or not it maps
    onto one of our four job statuses and whether or not we can find the
    job. Conecto has no screen showing where anyone is -- they said so
    outright -- so these rows are the only place the position, the
    kilometres and the time on site will ever exist. A receiver that
    updated the job's status and discarded the rest would leave a future
    "where is everyone" screen with nothing to draw.

    The job's own status is updated only when the mapping recognises the
    value. An unknown status must not silently complete somebody's work.
    """

    def post(self, request):
        from scheduling.models import Job

        from .mapping import job_status_from_conecto
        from .models import ConectoJob, ConectoJobEvent

        data = request.data if isinstance(request.data, dict) else {}
        vexar_job_id = str(self._first(data, "vexar_job_id", "vexarJobId") or "")
        conecto_job_id = str(self._first(data, "conecto_job_id", "job_id", "jobId") or "")
        status_value = str(self._first(data, "status") or "").strip().lower()

        if not status_value:
            return Response({"detail": "status is required."}, status=status.HTTP_400_BAD_REQUEST)

        job = None
        if vexar_job_id.isdigit():
            job = Job.objects.filter(pk=int(vexar_job_id)).first()
        if job is None and conecto_job_id:
            link = ConectoJob.objects.filter(conecto_job_id=conecto_job_id).first()
            job = link.job if link else None

        ConectoJobEvent.objects.create(
            job=job,
            vexar_job_id=vexar_job_id,
            conecto_job_id=conecto_job_id,
            # Conecto sends both now. technician_code is null for anyone
            # without a hand-set code in their admin screen -- which is
            # everybody who registered with a staff code -- so whichever
            # is populated is the identity that did the work.
            technician_code=str(
                self._first(
                    data, "technician_code", "technicianCode", "staff_code", "staffCode",
                ) or ""
            ),
            status=status_value[:20],
            occurred_at=parse_timestamp(self._first(data, "timestamp", "occurred_at")),
            lat=to_decimal(self._first(data, "lat", "latitude")),
            lng=to_decimal(self._first(data, "lng", "longitude")),
            km_traveled=to_decimal(self._first(data, "km_traveled", "kmTraveled")),
            duration_minutes=to_int(self._first(data, "duration_minutes", "durationMinutes")),
            payload=data,
        )

        # Return to Pool needs a reason (George, 14 Oct 2026): why does the
        # vehicle have to go back to the client? Refused here -- after the
        # event is recorded, before anything about the job changes -- so the
        # job stays booked and the app can ask the technician again.
        if status_value in RETURN_STATUSES and job is not None:
            from django.conf import settings as dj_settings

            min_len = getattr(dj_settings, "RETURN_REASON_MIN_LENGTH", 5)
            given = str(
                self._first(data, "return_reason", "reason", "note", "notes", "comment") or ""
            ).strip()
            if getattr(dj_settings, "CONECTO_RETURN_REASON_REQUIRED", True) and len(given) < min_len:
                return Response(
                    {
                        "code": "return_reason_required",
                        "detail": (
                            "Give a reason for returning this job to the pool: why does the vehicle need "
                            f"to go back to the client? At least {min_len} characters, in return_reason."
                        ),
                        "job": job.pk,
                        "returned_to_pool": False,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        if job is None:
            # Recorded, then refused: the event is kept because it is real,
            # but Conecto should know we could not attach it to anything.
            return Response(
                {"detail": "Unknown job; the update was recorded but not applied."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Scoped to the ONE technician this update is about, not every
        # ConectoJob row the job has. A job with two technicians on it
        # keeps one row each precisely so they can be at different stages
        # at once -- an update from one person's phone must not also mark
        # the other person's copy "checked in" just because they share a
        # job.
        technician_code = str(
            self._first(data, "technician_code", "technicianCode", "staff_code", "staffCode") or ""
        )
        if technician_code:
            ConectoJob.objects.filter(job=job, technician_code=technician_code).update(
                last_status=status_value[:20]
            )
        else:
            # No code on the payload to scope by (older Conecto versions, or
            # a malformed event). Safe to fall back to the old blanket
            # update only when there is exactly one row to blank onto --
            # the ordinary case, and the only one the single-technician
            # webhook ever had to handle. With two or more, guessing which
            # one this was about is worse than not updating: marking the
            # WRONG technician's row "cancelled", say, would make a later
            # real cancellation for them silently skipped as already done
            # (see _cancel_job's own guard). So this is recorded and left
            # alone rather than guessed at.
            rows = ConectoJob.objects.filter(job=job)
            if rows.count() == 1:
                rows.update(last_status=status_value[:20])
            elif rows.count() > 1:
                logger.warning(
                    "Job %s status update with no technician_code, and %d "
                    "technicians on it -- not applied to any ConectoJob row "
                    "(the job's own status above still updates).",
                    job.pk, rows.count(),
                )

        # Return instead of Complete (Rudolph, 6 Oct 2026): the technician
        # couldn't finish, so the job goes back to the pool with their note.
        if status_value in RETURN_STATUSES:
            from scheduling.pool import return_to_pool

            # Conecto's field is return_reason (7 Oct 2026 spec); the rest are
            # kept for the earlier draft of the Return button.
            note = str(
                self._first(data, "return_reason", "reason", "note", "notes", "comment") or ""
            ).strip()
            link = ConectoJob.objects.select_related("technician").filter(
                job=job, technician_code=technician_code
            ).first() if technician_code else None
            who = (
                (link.technician.first_name or link.technician.username)
                if link and link.technician_id else (technician_code or "the technician")
            )
            reason = f"Returned by {who}" + (f": {note}" if note else "")
            returned = return_to_pool(job, reason, kind="returned")
            return Response({"job": job.pk, "status": job.status, "returned_to_pool": returned})

        mapped = job_status_from_conecto(status_value)
        if mapped == Job.Status.CANCELLED:
            # A cancellation only ever originates HERE. Conecto has no
            # technician-facing cancel -- their action endpoint does start,
            # checkin and checkout and nothing else -- so a "cancelled"
            # status arriving on this webhook is confirmation of a cancelJob
            # call we made, not an instruction.
            #
            # Applying it would be wrong in exactly the case that matters: a
            # REASSIGNMENT cancels the old job before scheduling the new one,
            # and letting that confirmation set our status to cancelled would
            # cancel work that is going ahead with somebody else.
            mapped = None

        if mapped and mapped != job.status:
            with suppressed():
                job.status = mapped
                job.save(update_fields=["status", "updated_at"])

        # Completed now names the technician's job card (7 Oct 2026): tie a
        # card that arrived without a job to this one.
        card_id = self._first(data, "job_card_id", "jobCardId")
        if card_id:
            from .job_cards import link_completed_job

            link_completed_job(job, card_id)

        return Response({"job": job.pk, "status": job.status})


class JobChecklistCompletedWebhook(ConectoWebhook):
    """A technician submitted their stock tick-list for the day.

    Fires once per technician per day. The field that matters is
    `any_missing`: a technician may go through the list with items
    unchecked after confirming, so it is entirely possible for somebody to
    be on the road without a router. That is a support follow-up, and this
    row is what it would be driven from.
    """

    def post(self, request):
        from .models import ConectoStockChecklist

        data = request.data if isinstance(request.data, dict) else {}
        technician_code = str(self._first(data, "technician_code", "technicianCode") or "")
        effective_date = parse_date_only(self._first(data, "effective_date", "effectiveDate"))

        if not technician_code or effective_date is None:
            return Response(
                {"detail": "technician_code and effective_date (YYYY-MM-DD) are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        items = data.get("items")
        if not isinstance(items, list):
            items = []
        any_missing = data.get("any_missing")
        if any_missing is None:
            # Derive it rather than assume nothing is missing: the field
            # that triggers a follow-up must not default to "all fine".
            any_missing = any(not entry.get("confirmed") for entry in items if isinstance(entry, dict))

        checklist, created = ConectoStockChecklist.objects.update_or_create(
            technician_code=technician_code,
            effective_date=effective_date,
            defaults={
                "results": items,
                "any_missing": bool(any_missing),
                "completed_at": parse_timestamp(self._first(data, "completed_at", "completedAt"))
                or timezone.now(),
            },
        )
        if created:
            # A checklist completed for a day we never set one for. Not an
            # error -- Conecto may hold a standing list -- but worth saying,
            # because the usual cause is a setStockChecklist that failed.
            logger.warning(
                "Conecto checklist completed for %s on %s, but Skybre never sent a list "
                "for that day.", technician_code, effective_date,
            )

        if checklist.any_missing:
            logger.warning(
                "Conecto checklist for %s on %s went out with unticked items: %s",
                technician_code, effective_date,
                [e.get("item") for e in items if isinstance(e, dict) and not e.get("confirmed")],
            )

        return Response({"technician_code": technician_code, "any_missing": checklist.any_missing})


#: A readable subject when Conecto sends none. Their categories are about
#: field work and ours are about customer enquiries, so these are not
#: mapped onto a department -- they are just better than a list of
#: identical rows nobody triages.
CATEGORY_SUBJECT = {
    "quote_needed": "Quote needed",
    "return_booking": "Return booking needed",
    "stock_needed": "Stock needed",
    # Ticked on the job card (7 Oct 2026): follow-up work to schedule.
    "additional_work": "Additional work needed",
    "other": "Job feedback from a technician",
}


class InternalTicketCreatedWebhook(ConectoWebhook):
    """A technician filed feedback on a job: quote, stock, return booking.

    ITS OWN RECEIVER, NEVER SHARED WITH TicketCreatedWebhook. Conecto
    asked for that explicitly and they are right: the client receiver
    looks up a customer by client code and refuses without one, which is
    exactly the wrong shape here. One endpoint doing both would mean one
    branch away from a technician's note becoming a customer's ticket.

    THE TICKET IT CREATES HAS NO CUSTOMER. Not "a customer that is hidden"
    -- none. The job it came from usually has one, and that customer is
    reachable through the job link for anybody who needs it, but the
    ticket row itself carries NULL so the portal's
    filter(customer=<profile>) can never match it. A check constraint on
    tickets.Ticket holds that to be true in both directions.
    """

    def post(self, request):
        from scheduling.models import Job

        from .models import ConectoInternalTicket

        data = request.data if isinstance(request.data, dict) else {}
        conecto_id = str(
            self._first(data, "conecto_ticket_id", "ticket_id", "ticketId") or ""
        )
        if not conecto_id:
            return Response(
                {"detail": "conecto_ticket_id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        existing = ConectoInternalTicket.objects.select_related("ticket").filter(
            conecto_ticket_id=conecto_id
        ).first()
        if existing:
            # A replay. Answer as though it worked, because from their side
            # it did, and a 400 would be logged and dropped.
            return Response(
                {"ticket_number": existing.ticket.ticket_number, "duplicate": True}
            )

        job = None
        vexar_job_id = str(self._first(data, "vexar_job_id", "vexarJobId") or "")
        if vexar_job_id.isdigit():
            job = Job.objects.filter(pk=int(vexar_job_id)).first()
        if job is None:
            raw = str(self._first(data, "job_id", "jobId") or "")
            if raw:
                from .models import ConectoJob

                link = ConectoJob.objects.select_related("job").filter(
                    conecto_job_id=raw
                ).first()
                job = link.job if link is not None else None

        subject = (self._first(data, "subject", "title") or "").strip()[:255]
        category = str(self._first(data, "category") or "").strip()[:32]
        if not subject:
            # Named after the category rather than left blank: a list of
            # tickets all reading "Job feedback" is a list nobody triages.
            subject = CATEGORY_SUBJECT.get(category, "Job feedback from a technician")

        with transaction.atomic():
            # suppressed(): this came FROM Conecto. The outbound ticket
            # signal refuses internal tickets outright as well -- both,
            # because the cost of either failing alone is a job-feedback
            # note appearing in a customer's app.
            with suppressed():
                ticket = Ticket.objects.create(
                    customer=None,
                    is_internal=True,
                    subject=subject,
                    description=self._first(data, "description", "body") or "",
                    department=Ticket.Department.SUPPORT,
                    priority=Ticket.Priority.MEDIUM,
                    status=Ticket.Status.OPEN,
                )
                ConectoInternalTicket.objects.create(
                    ticket=ticket,
                    conecto_ticket_id=conecto_id,
                    job=job,
                    vexar_job_id=vexar_job_id[:64],
                    technician_code=str(
                        self._first(data, "technician_code", "technicianCode") or ""
                    )[:40],
                    category=category,
                    job_card_id=str(self._first(data, "job_card_id", "jobCardId") or "")[:128],
                )

        return Response(
            {"ticket_number": ticket.ticket_number, "vexar_ticket_id": ticket.ticket_number},
            status=status.HTTP_201_CREATED,
        )


class InternalTicketRepliedWebhook(ConectoWebhook):
    """A technician answered on their own job-feedback ticket.

    Looked up ONLY in ConectoInternalTicket. An id that belongs to a
    client ticket is simply not found here, which is the point of the two
    tables: Conecto's two kinds of ticket share an id namespace, so the
    table is the only thing that can tell them apart.

    The reply is stored with external_author set and is_internal False --
    an ordinary message in the thread, attributed to the technician who
    wrote it. author stays null because the Conecto app is not this
    platform's account system, and a message with no name against it would
    read as though the office had written it to itself.
    """

    def post(self, request):
        from .models import ConectoInternalTicket

        data = request.data if isinstance(request.data, dict) else {}
        conecto_id = str(
            self._first(data, "conecto_ticket_id", "ticket_id", "ticketId") or ""
        )
        message = str(self._first(data, "message", "text", "body", "reply") or "").strip()
        author = str(self._first(data, "author_name", "authorName") or "").strip()[:150]

        if not message:
            return Response(
                {"detail": "message is required."}, status=status.HTTP_400_BAD_REQUEST
            )

        link = ConectoInternalTicket.objects.select_related("ticket").filter(
            conecto_ticket_id=conecto_id
        ).first()
        if link is None:
            return Response(
                {"detail": "Unknown internal ticket."}, status=status.HTTP_404_NOT_FOUND
            )

        ticket = link.ticket
        last = ticket.comments.order_by("-created_at").first()
        if last is not None and last.message == message and last.external_author == (
            author or "Technician"
        ):
            return Response({"ticket_number": ticket.ticket_number, "duplicate": True})

        with suppressed():
            TicketComment.objects.create(
                ticket=ticket,
                author=None,
                message=message,
                is_internal=False,
                external_author=author or "Technician",
            )

        return Response(
            {"ticket_number": ticket.ticket_number}, status=status.HTTP_201_CREATED
        )


class TicketNoteAddedWebhook(ConectoWebhook):
    """Skybre staff added an internal note on a ticket, from the Conecto app.

    `ticket_type` says which table finds it -- CLIENT tickets can be
    matched on either `ticket_id` (Conecto's own) or `vexar_ticket_id`
    (ours), same as ticket-replied, because a client ticket can start on
    either side. INTERNAL tickets are matched on `ticket_id` alone,
    same as internal-ticket-replied and for the same reason its own
    docstring gives: Conecto's two kinds of ticket share one id
    namespace, and only ConectoInternalTicket can tell an internal id
    from a client one -- there is no vexar-side fallback because an
    internal ticket is never created anywhere but Conecto.

    is_internal is hard-set True regardless of what the payload says.
    This endpoint's only reason to exist is internal notes; trusting an
    inbound field to turn that off would be the exact hole
    ticket-replied's own is_internal guard exists to close, just from the
    other direction.

    job_id/vexar_job_id are resolved the same way
    InternalTicketCreatedWebhook resolves them (vexar_job_id as our own
    pk first, then job_id against ConectoJob's own id) and kept on the
    comment for context -- but a job that cannot be resolved is not a
    reason to lose the note. Only the ticket has to be found.
    """

    def post(self, request):
        from scheduling.models import Job

        from .models import ConectoInternalTicket, ConectoJob

        data = request.data if isinstance(request.data, dict) else {}
        conecto_id = str(self._first(data, "ticket_id", "ticketId") or "")
        vexar_ref = str(self._first(data, "vexar_ticket_id", "ticket_number") or "")
        ticket_type = str(self._first(data, "ticket_type", "ticketType") or "").strip().lower()
        note = str(self._first(data, "note", "message", "text") or "").strip()
        author_name = str(self._first(data, "author_name", "authorName") or "").strip()[:120]
        author_role = str(self._first(data, "author_role", "authorRole") or "").strip()[:40]

        if not note:
            return Response(
                {"detail": "note is required."}, status=status.HTTP_400_BAD_REQUEST
            )

        if ticket_type == "internal":
            link = ConectoInternalTicket.objects.select_related("ticket").filter(
                conecto_ticket_id=conecto_id
            ).first()
            ticket = link.ticket if link else None
        else:
            link = None
            if conecto_id:
                link = ConectoTicket.objects.select_related("ticket").filter(
                    conecto_ticket_id=conecto_id
                ).first()
            ticket = link.ticket if link else None
            if ticket is None and vexar_ref:
                ticket = Ticket.objects.filter(ticket_number=vexar_ref).first()

        if ticket is None:
            return Response(
                {"detail": "Unknown ticket."}, status=status.HTTP_404_NOT_FOUND
            )

        if author_name and author_role:
            external_author = f"{author_name} ({author_role})"[:150]
        else:
            external_author = author_name or "Staff"

        job = None
        vexar_job_id = str(self._first(data, "vexar_job_id", "vexarJobId") or "")
        if vexar_job_id.isdigit():
            job = Job.objects.filter(pk=int(vexar_job_id)).first()
        if job is None:
            raw_job_id = str(self._first(data, "job_id", "jobId") or "")
            if raw_job_id:
                job_link = ConectoJob.objects.select_related("job").filter(
                    conecto_job_id=raw_job_id
                ).first()
                job = job_link.job if job_link is not None else None

        tagged_raw = data.get("tagged")
        tagged_names = (
            [str(n).strip() for n in tagged_raw if str(n).strip()]
            if isinstance(tagged_raw, list) else []
        )
        attachments_raw = data.get("attachments")
        attachments = []
        if isinstance(attachments_raw, list):
            for entry in attachments_raw:
                if isinstance(entry, dict) and entry.get("url"):
                    attachments.append({
                        "type": str(entry.get("type") or "")[:40],
                        "url": str(entry["url"])[:2000],
                    })

        # Same replay guard as every other inbound webhook: Conecto logs
        # and drops a failure rather than retrying, so an identical note
        # from the same person right after the last one is far more
        # likely to be a duplicate delivery than a repeated note.
        last = ticket.comments.order_by("-created_at").first()
        if last is not None and last.message == note and last.external_author == external_author:
            return Response({"ticket_number": ticket.ticket_number, "duplicate": True})

        with suppressed():
            TicketComment.objects.create(
                ticket=ticket,
                author=None,
                message=note,
                is_internal=True,
                external_author=external_author,
                job=job,
                tagged_names=tagged_names,
                attachments=attachments,
            )

        return Response(
            {"ticket_number": ticket.ticket_number}, status=status.HTTP_201_CREATED
        )


class TechnicianPositionUpdatedWebhook(ConectoWebhook):
    """A technician's phone reporting where it is, once a minute.

    Conecto sends these only between the Start tap and the Check-in tap.
    That is the window the office cares about -- somebody is on their way
    to a customer and the question is how far off they are -- and it is
    also the window in which a technician has agreed to be located,
    because they started the job. Outside it the phone reports nothing,
    which is a design choice of theirs worth keeping rather than asking
    them to widen.

    WHAT THIS REFUSES.

    A reading with no usable coordinate, because a pin at a wrong place
    is worse than no pin. That includes the classic null-island fix at
    exactly 0,0 -- which is Atlantic ocean off Ghana, and is what a GPS
    chip reports when it has no fix at all rather than an honest error.

    A reading with no identifiable technician. Conecto sends
    technician_code OR staff_code depending on how that phone was
    registered, and either is a valid identity; neither is not.

    WHAT THIS TOLERATES.

    An unknown job. Position and job are separate facts and the position
    is still true -- a 404 here would throw away a real reading because
    of a bookkeeping mismatch, and Conecto logs failures rather than
    retrying them, so it would be thrown away permanently.

    A duplicate. The unique constraint catches a replayed ping, and the
    answer is 200 with duplicate: true, not an error, because from
    Conecto's side the delivery did work.
    """

    def post(self, request):
        from scheduling.models import Job

        from .models import ConectoJob, ConectoTechnicianPosition

        data = request.data if isinstance(request.data, dict) else {}

        code = str(
            self._first(
                data, "technician_code", "technicianCode", "staff_code", "staffCode",
            ) or ""
        ).strip()
        if not code:
            return Response(
                {"detail": "technician_code or staff_code is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        lat = to_decimal(self._first(data, "lat", "latitude"))
        lng = to_decimal(self._first(data, "lng", "longitude"))
        if lat is None or lng is None:
            return Response(
                {"detail": "lat and lng are required."}, status=status.HTTP_400_BAD_REQUEST
            )
        if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
            return Response(
                {"detail": "lat/lng out of range."}, status=status.HTTP_400_BAD_REQUEST
            )
        if lat == 0 and lng == 0:
            # Not a place anybody in the Free State is. A chip with no fix
            # reports this, and drawing it would put a technician in the
            # Gulf of Guinea with a confident-looking timestamp.
            logger.warning("Conecto position for %s discarded: null-island fix", code)
            return Response(
                {"detail": "0,0 is not a fix."}, status=status.HTTP_400_BAD_REQUEST
            )

        recorded_at = parse_timestamp(
            self._first(data, "recorded_at", "recordedAt", "timestamp")
        )
        if recorded_at is None:
            # Falling back to now() is a real loss of fidelity, so it is
            # said out loud rather than absorbed. Ordering by our clock is
            # wrong the moment a phone flushes a backlog.
            logger.warning("Conecto position for %s had no usable recorded_at", code)
            recorded_at = timezone.now()

        # job_id HERE IS CONECTO'S, NOT OURS. Their written spec for this
        # webhook called it "our job id", which contradicted
        # job-status-changed, where job_id is theirs and vexar_job_id is
        # ours. They checked the live code on 2026-09-21: the app sends the
        # Firestore job document id it already has open, and there is no
        # vexar_job_id on this payload at all.
        #
        # So the Conecto id is tried FIRST and a numeric reading only as a
        # fallback -- the reverse of what this did while the spec was the
        # only evidence. A Firestore auto-id is twenty characters of mixed
        # case, so an all-digit one is vanishingly unlikely, but "unlikely"
        # is a bad reason to look up the wrong table first when the right
        # one is now known.
        #
        # The fallback stays because they offered to add vexar_job_id to
        # every ping. If that arrives, it is read here already.
        raw_job = str(self._first(data, "vexar_job_id", "vexarJobId", "job_id", "jobId") or "")
        job = None
        vexar_job_id = ""
        conecto_job_id = ""
        if raw_job:
            link = ConectoJob.objects.select_related("job").filter(
                conecto_job_id=raw_job
            ).first()
            if link is not None:
                job = link.job
                conecto_job_id = raw_job
        if job is None and raw_job.isdigit():
            job = Job.objects.filter(pk=int(raw_job)).first()
            vexar_job_id = raw_job

        position, created = ConectoTechnicianPosition.objects.get_or_create(
            technician_code=code[:40],
            recorded_at=recorded_at,
            defaults={
                "job": job,
                "vexar_job_id": vexar_job_id[:64],
                "conecto_job_id": conecto_job_id[:128],
                "lat": lat,
                "lng": lng,
                "payload": data,
            },
        )

        return Response(
            {"recorded": True, "duplicate": not created, "id": position.pk},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


#: How long one viewing session may last, however diligently Conecto poll.
#: Theirs to request, ours to enforce -- they asked for it that way, because
#: a phone that loses signal mid-session never sends `stop`.
BANDWIDTH_SESSION_SECONDS = 300

#: The poll endpoint is called every five to ten seconds per watching client,
#: from a small number of Cloud Function addresses. The webhook throttle of
#: 120/min is sized for events, not for this: a dozen clients watching at once
#: would exceed it and start getting 429s, which would look to a customer like
#: their line had stopped reporting. Its own rate, and its own setting.
class BandwidthThrottle(WebhookThrottle):
    scope = "conecto_bandwidth"

    def get_rate(self):
        return getattr(settings, "CONECTO_BANDWIDTH_RATE", "1200/min")


class ConectoBandwidthView(ConectoWebhook):
    """Shared plumbing for the three bandwidth calls."""

    throttle_classes = [BandwidthThrottle]

    def _session(self, data):
        from .models import ConectoBandwidthSession

        raw = str(self._first(data, "session_id", "sessionId") or "")
        if not raw:
            return None
        try:
            uuid.UUID(raw)
        except (ValueError, AttributeError, TypeError):
            # A malformed id is not a database error. Postgres would raise on
            # a bad UUID literal and turn a client mistake into a 500.
            return None
        return (
            ConectoBandwidthSession.objects.select_related("customer")
            .filter(session_id=raw)
            .first()
        )

    def _state_for(self, customer):
        """What we can honestly say about this customer's line right now.

        Returns (state, reason, sessions).

          live         a router-sourced rate, accurate to the second.
          warming      a reader is running but has no rate yet. A rate needs
                       two samples, so the first second of every session is
                       this -- and returning 0.0 Mbps instead would read, on
                       the screen somebody opened to check their line, as
                       "your line is dead".
          offline      no active RADIUS session. Not zero: nothing to measure.
          unavailable  we cannot get a live figure for this customer at all.

        "accounting" NEVER maps to live. That figure is an average over the
        NAS's interim interval, so a speed test reads low on it, and
        presenting it as a live number would be the same mistake as a map pin
        that looks current and is an hour old. It is warming at best.
        """
        from radiusauth.usage import live_state, request_live_readings, usernames_for_customer

        # NO PER-CUSTOMER OPT-IN ON THIS PATH. The Conecto session IS the
        # grant: it was created by an authenticated call, on behalf of a
        # client who opened the screen in their own app, and it expires in
        # five minutes on its own. There is nothing to switch on beforehand
        # and nothing to switch off afterwards.
        #
        # DELIBERATELY NOT toggling Customer.live_bandwidth_public to
        # achieve this, which is the obvious implementation and the wrong
        # one. That flag also governs the no-login usage LINK staff send to
        # customers. Switching it on for five minutes would hand public link
        # access to people who were never granted it, and switching it off
        # again afterwards would REVOKE it from every customer staff had
        # deliberately enabled it for -- silently, as a side effect of an
        # unrelated feature. One feature quietly turning another one off is
        # not a trade worth making for a flag this path does not need.
        #
        # The resource argument the flag existed for does not apply here
        # either: cost is bounded by ROUTERS, not customers. live_broker
        # holds one connection per device behind an advisory lock, so ten
        # clients watching one router is one connection, and it closes about
        # twenty seconds after the last poll.

        usernames = usernames_for_customer(customer)
        sessions = live_state(usernames)
        if not sessions:
            return "offline", "", []

        # Starts the reader. Deliberately does NOT call
        # touch_live_bandwidth_view(): that holds back the idle clock on the
        # public usage LINK, and somebody watching in the app is not somebody
        # using the link. Extending a link's life because of app traffic
        # would keep alive an access nobody is exercising.
        watched = request_live_readings(customer)

        if watched == 0:
            # No device with the RouterOS API enabled, so nothing will ever
            # produce a live rate here however long they poll.
            return "unavailable", "no_api_device", sessions

        if any(s.get("rate_source") == "router" for s in sessions):
            return "live", "", sessions
        return "warming", "", sessions

    @staticmethod
    def _mbps(bits_per_second):
        return round((bits_per_second or 0) / 1_000_000, 2)

    def _payload(self, session, state, reason, sessions):
        """Their two fields, plus the breakdown they asked us to include.

        download_mbps/upload_mbps are the TOTAL across every login, because
        their schema assumes one pair and several of our customers have more
        than one concurrent session. The per-login figures ride along in
        `sessions` for when they want them.
        """
        live_rows = [s for s in sessions if s.get("rate_source") == "router"]
        sampled_at = max((s["rate_measured_at"] for s in live_rows), default=None)
        return {
            "state": state,
            # Present even when the state makes them meaningless, so their
            # client never has to guard a missing key. The state is what
            # says whether to believe them.
            "download_mbps": self._mbps(sum(s["download_bps"] for s in live_rows)),
            "upload_mbps": self._mbps(sum(s["upload_bps"] for s in live_rows)),
            "sampled_at": sampled_at.isoformat() if sampled_at else None,
            "expires_at": session.expires_at.isoformat() if session else None,
            # Not an error code they must handle -- a note for their logs and
            # ours when "unavailable" needs explaining.
            "reason": reason,
            "sessions": [
                {
                    "username": s["username"],
                    "download_mbps": self._mbps(s["download_bps"]),
                    "upload_mbps": self._mbps(s["upload_bps"]),
                    "ip_address": s["ip_address"],
                    "started_at": s["started_at"].isoformat() if s["started_at"] else None,
                    "rate_source": s["rate_source"],
                }
                for s in sessions
            ],
        }


class BandwidthStartView(ConectoBandwidthView):
    """A client opened the live speed screen."""

    def post(self, request):
        from .models import ConectoBandwidthSession

        data = request.data if isinstance(request.data, dict) else {}
        client_code = self._first(data, "client_code", "clientCode")
        customer = self._customer(client_code)
        if customer is None:
            return Response(
                {"detail": f"No customer with client code {client_code!r}."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # One live session per customer. A client who backgrounds the app and
        # reopens it should resume rather than accumulate sessions nobody
        # will ever stop -- and the expiry is what limits them either way.
        now = timezone.now()
        ConectoBandwidthSession.objects.filter(
            customer=customer, stopped_at__isnull=True, expires_at__gt=now
        ).update(stopped_at=now)

        session = ConectoBandwidthSession.objects.create(
            customer=customer,
            expires_at=now + timedelta(seconds=BANDWIDTH_SESSION_SECONDS),
        )
        state, reason, sessions = self._state_for(customer)

        # The state is returned from `start` as well, so an app that is about
        # to learn the line is offline never starts a poll loop for it.
        payload = self._payload(session, state, reason, sessions)
        payload["session_id"] = str(session.session_id)
        return Response(payload, status=status.HTTP_201_CREATED)


class BandwidthPollView(ConectoBandwidthView):
    """What is the line doing right now."""

    def post(self, request):
        from .models import ConectoBandwidthSession

        data = request.data if isinstance(request.data, dict) else {}
        session = self._session(data)
        if session is None:
            return Response(
                {"detail": "Unknown session_id."}, status=status.HTTP_404_NOT_FOUND
            )

        now = timezone.now()
        if session.stopped_at is not None or session.expires_at <= now:
            # 200 rather than an error: from the app's side this is a normal
            # end, and their screen should say so and offer to start again
            # rather than show a failure.
            if session.stopped_at is None:
                ConectoBandwidthSession.objects.filter(pk=session.pk).update(stopped_at=now)
            return Response(self._payload(session, "expired", "", []))

        ConectoBandwidthSession.objects.filter(pk=session.pk).update(
            last_polled_at=now, poll_count=models.F("poll_count") + 1,
        )
        state, reason, sessions = self._state_for(session.customer)
        return Response(self._payload(session, state, reason, sessions))


class BandwidthStopView(ConectoBandwidthView):
    """The client left the screen.

    An optimisation, not a guarantee -- the expiry ends the session whether
    or not this arrives, which is how Conecto asked for it. Answering 200 for
    an already-stopped or unknown session on purpose: there is nothing useful
    a client can do about a failed stop, and an error would be logged and
    dropped on their side anyway.
    """

    def post(self, request):
        from .models import ConectoBandwidthSession

        data = request.data if isinstance(request.data, dict) else {}
        session = self._session(data)
        if session is not None and session.stopped_at is None:
            ConectoBandwidthSession.objects.filter(pk=session.pk).update(
                stopped_at=timezone.now()
            )
        return Response({"stopped": True})


#: How long a minted document URL stays good. Long enough for a phone on a
#: poor connection to follow the redirect and render a PDF, short enough
#: that a link pasted into a group chat is dead before it travels.
DOCUMENT_TOKEN_SECONDS = 180


def _document_base(request):
    """The origin to build a document URL on.

    request.build_absolute_uri gets this right only if the proxy headers are
    trusted, and behind nginx an untrusted deployment would hand Conecto an
    http:// link to a customer's tax invoice. So a setting wins when there
    is one, and the request is the fallback -- with the scheme forced to
    https unless this is a local development host, because there is no
    correct reason to send that link over plain http.
    """
    # SITE_URL is the fallback because it already exists and is already the
    # right answer: it is the origin every customer email links to, so it is
    # by definition a host the customer's phone can reach.
    #
    # CONECTO_PUBLIC_BASE_URL now has its config() line and defaults to the
    # portal host (7 Oct 2026): SITE_URL is skybre.co.za, the marketing
    # site, which 404s /api/public/conecto-document/.
    configured = (
        getattr(settings, "CONECTO_PUBLIC_BASE_URL", "")
        or getattr(settings, "SITE_URL", "")
    )
    if configured:
        return configured.rstrip("/")
    base = request.build_absolute_uri("/").rstrip("/")
    if base.startswith("http://") and "localhost" not in base and "127.0.0.1" not in base:
        base = "https://" + base[len("http://"):]
    return base


class ConectoDocumentUrlView(ConectoWebhook):
    """Shared plumbing for the two URL-minting calls."""

    def _mint(self, request, *, kind, customer, invoice=None, period=""):
        from .models import ConectoDocumentToken

        token = ConectoDocumentToken.objects.create(
            kind=kind, customer=customer, invoice=invoice, period=period,
            expires_at=timezone.now() + timedelta(seconds=DOCUMENT_TOKEN_SECONDS),
        )
        return Response({
            "pdf_url": f"{_document_base(request)}/api/public/conecto-document/{token.token}/",
            "expires_at": token.expires_at.isoformat(),
            "expires_in_seconds": DOCUMENT_TOKEN_SECONDS,
        })


class ConectoInvoiceUrlView(ConectoDocumentUrlView):
    """A fresh URL for one invoice PDF, at the moment the client taps.

    Keyed on the invoice NUMBER rather than our primary key, because the
    number is what Conecto hold -- it is their upsert key -- and asking them
    to store a second identifier would be asking for a bug.
    """

    def post(self, request):
        from billing.models import Invoice

        data = request.data if isinstance(request.data, dict) else {}
        number = str(self._first(data, "invoice_number", "invoiceNumber") or "").strip()
        if not number:
            return Response(
                {"detail": "invoice_number is required."}, status=status.HTTP_400_BAD_REQUEST
            )

        invoice = Invoice.objects.select_related("customer").filter(number=number).first()
        if invoice is None:
            return Response(
                {"detail": f"No invoice numbered {number!r}."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if invoice.status not in Invoice.ISSUED_STATUSES:
            # The same rule as the push. A client cannot be handed a link to
            # a draft or a cancelled document even by guessing its number.
            return Response(
                {"detail": "That document is not an issued invoice."},
                status=status.HTTP_404_NOT_FOUND,
            )

        return self._mint(request, kind="invoice", customer=invoice.customer, invoice=invoice)


class ConectoStatementUrlView(ConectoDocumentUrlView):
    """A fresh URL for one customer's statement PDF."""

    def post(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        client_code = self._first(data, "client_code", "clientCode")
        customer = self._customer(client_code)
        if customer is None:
            return Response(
                {"detail": f"No customer with client code {client_code!r}."},
                status=status.HTTP_404_NOT_FOUND,
            )
        period = str(self._first(data, "period") or "").strip()[:7]
        return self._mint(request, kind="statement", customer=customer, period=period)


class ConectoQuoteUrlView(ConectoDocumentUrlView):
    """A fresh URL for one quote's PDF (7 Oct 2026). Keyed on the quote
    number Conecto holds -- which may since have become an invoice, in
    which case the document as it now stands is what opens."""

    def post(self, request):
        from billing.models import Invoice

        from .models import ConectoQuote

        data = request.data if isinstance(request.data, dict) else {}
        number = str(self._first(data, "quote_number", "quoteNumber") or "").strip()
        if not number:
            return Response({"detail": "quote_number is required."}, status=status.HTTP_400_BAD_REQUEST)
        link = ConectoQuote.objects.select_related("invoice__customer").filter(quote_number=number).first()
        invoice = link.invoice if link is not None else (
            Invoice.objects.select_related("customer").filter(number=number, status=Invoice.Status.QUOTE).first()
        )
        if invoice is None or invoice.status in (Invoice.Status.DRAFT, Invoice.Status.CANCELLED):
            return Response({"detail": f"No quote numbered {number!r}."}, status=status.HTTP_404_NOT_FOUND)
        return self._mint(request, kind="quote", customer=invoice.customer, invoice=invoice)


class ConectoVehiclesView(ConectoWebhook):
    """The vehicles a technician can log a trip or a fuel slip against
    (7 Oct 2026): every active vehicle, theirs first."""

    def post(self, request):
        from fleet.conecto_logs import find_staff, vehicle_list

        data = request.data if isinstance(request.data, dict) else {}
        user = find_staff(self._first(data, "staff_code", "staffCode"), self._first(data, "technician_code"))
        return Response({"vehicles": vehicle_list(user)})


class JobCardWebhook(ConectoWebhook):
    """POST /api/webhooks/conecto/job-card/ -- a technician's digital job
    card (7 Oct 2026). 201 {job_card_id} when new, 200 with the same id for a
    repeat (Conecto retries until accepted). See conecto.job_cards."""

    def post(self, request):
        from .job_cards import record_job_card

        data = request.data if isinstance(request.data, dict) else {}
        try:
            card, created = record_job_card(data)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        body = {"job_card_id": str(card.pk)}
        if not created:
            body["duplicate"] = True
        return Response(body, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class VehicleLogWebhook(ConectoWebhook):
    """Shared answer for the trip and fuel webhooks: 201 + log_id when made,
    200 + the same log_id for a repeat, 400 / 404 with a reason."""

    recorder = None

    def post(self, request):
        from fleet.conecto_logs import LogError

        data = request.data if isinstance(request.data, dict) else {}
        try:
            row, created = type(self).recorder(data)
        except LogError as exc:
            return Response({"detail": str(exc)}, status=exc.status)
        body = {"log_id": str(row.pk)}
        if not created:
            body["duplicate"] = True
        return Response(body, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class VehicleKmLogWebhook(VehicleLogWebhook):
    @staticmethod
    def recorder(data):
        from fleet.conecto_logs import record_trip

        return record_trip(data)


class VehicleFuelLogWebhook(VehicleLogWebhook):
    @staticmethod
    def recorder(data):
        from fleet.conecto_logs import record_fuel

        return record_fuel(data)


class SalesSummaryView(ConectoWebhook):
    """The sales dashboard figures for the app (7 Oct 2026). staff_code =
    that person's own leads; null = the whole team, with by_person. See
    sales.conecto_summary for every definition."""

    def post(self, request):
        from django.contrib.auth import get_user_model
        from django.utils.dateparse import parse_date

        from sales.conecto_summary import summary

        data = request.data if isinstance(request.data, dict) else {}
        date_from = parse_date(str(data.get("date_from") or ""))
        date_to = parse_date(str(data.get("date_to") or ""))
        if date_from is None or date_to is None:
            return Response({"detail": "date_from and date_to are required, as YYYY-MM-DD."},
                            status=status.HTTP_400_BAD_REQUEST)
        if date_to < date_from:
            return Response({"detail": "date_to is before date_from."}, status=status.HTTP_400_BAD_REQUEST)
        user = None
        code = str(data.get("staff_code") or "").strip()
        if code:
            user = get_user_model().objects.filter(staff_code__iexact=code).first()
            if user is None:
                return Response({"detail": f"No staff member with code {code!r}."},
                                status=status.HTTP_404_NOT_FOUND)
        return Response(summary(user, date_from, date_to))


class SalesAppView(ConectoWebhook):
    """Shared: staff code -> user, errors -> 4xx with a detail."""

    def handle(self, request, fn):
        from sales.conecto_api import SalesApiError, staff_user

        data = request.data if isinstance(request.data, dict) else {}
        try:
            user = staff_user(data.get("staff_code"))
            return Response(fn(user, data))
        except SalesApiError as exc:
            return Response({"detail": str(exc)}, status=exc.status)


class SalesLeadsView(SalesAppView):
    """POST /api/conecto/sales/leads/ -- {staff_code, view, stage, search,
    page, scope}; 25 a page."""

    def post(self, request):
        from sales.conecto_api import list_leads

        return self.handle(request, list_leads)


class SalesLeadView(SalesAppView):
    """POST /api/conecto/sales/lead/ -- {staff_code, lead_id}: every field,
    the timeline (newest first) and a short-lived quote PDF link."""

    def post(self, request):
        from sales.conecto_api import get_lead, lead_detail

        return self.handle(request, lambda user, data: lead_detail(request, get_lead(user, data.get("lead_id"))))


class SalesLeadActionView(SalesAppView):
    """POST /api/conecto/sales/lead/action/ -- note / follow_up / lost."""

    def post(self, request):
        from sales.conecto_api import apply_action, get_lead, lead_detail

        def act(user, data):
            lead = apply_action(user, get_lead(user, data.get("lead_id")), data)
            return {"success": True, "lead": lead_detail(request, lead)}

        return self.handle(request, act)


class LeadQuotePdfView(APIView):
    """The PDF behind a sales lead's quote_pdf_url. Unauthenticated: the
    signed, 15-minute token in the URL is the credential."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [WebhookThrottle]

    def get(self, request, token):
        from django.http import HttpResponse

        from sales.conecto_api import lead_from_quote_token
        from sales.quote import render_quote_pdf
        from sales.workflow import quote_number

        lead = lead_from_quote_token(token)
        if lead is None or not lead.quote_sent_at:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        response = HttpResponse(render_quote_pdf(lead), content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{quote_number(lead)}.pdf"'
        return response


class ConectoDocumentView(APIView):
    """The PDF itself, fetched by the client's own phone.

    UNAUTHENTICATED BY DESIGN, and the token is the credential -- the same
    arrangement as the public usage page. Three things keep that honest:
    the token is unguessable, it dies in three minutes, and it names exactly
    one document rather than a customer's whole history.

    The PDF comes from notifications.services._build_attachment, which is
    the identical call the emailer and the staff-side PDF views make. A
    second rendering path that happened to look similar is how a layout fix
    lands in one place and misses another.
    """

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [WebhookThrottle]

    def get(self, request, token):
        from django.http import HttpResponse

        from notifications import services
        from notifications.models import EmailTemplate

        from .models import ConectoDocumentToken

        row = (
            ConectoDocumentToken.objects.select_related("customer", "invoice")
            .filter(token=token)
            .first()
        )
        # One answer for missing, expired and malformed alike: a caller
        # holding a dead token learns nothing about whether it was ever real.
        if row is None or not row.is_valid:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        if row.kind == ConectoDocumentToken.Kind.QUOTE:
            invoice = row.invoice
            if invoice is None or invoice.status in ("draft", "cancelled"):
                return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
            key_by_status = {
                "quote": EmailTemplate.Key.QUOTE,
                "proforma": EmailTemplate.Key.PROFORMA,
            }
            filename, pdf_bytes, _ = services._build_attachment(
                key_by_status.get(invoice.status, EmailTemplate.Key.INVOICE), invoice.customer, invoice=invoice
            )
        elif row.kind == ConectoDocumentToken.Kind.INVOICE:
            invoice = row.invoice
            if invoice is None or invoice.status not in _issued_statuses():
                # Checked again at fetch time, not only at mint time. An
                # invoice cancelled in the three minutes between the two is
                # exactly the document that must not open.
                return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
            key_by_status = {
                "quote": EmailTemplate.Key.QUOTE,
                "proforma": EmailTemplate.Key.PROFORMA,
            }
            template_key = key_by_status.get(invoice.status, EmailTemplate.Key.INVOICE)
            filename, pdf_bytes, _ = services._build_attachment(
                template_key, invoice.customer, invoice=invoice
            )
        else:
            filename, pdf_bytes, _ = services._build_attachment(
                EmailTemplate.Key.STATEMENT, row.customer
            )

        ConectoDocumentToken.objects.filter(pk=row.pk).update(fetched_at=timezone.now())

        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{filename}"'
        return response


def _issued_statuses():
    from billing.models import Invoice

    return set(Invoice.ISSUED_STATUSES)


# --- parsing helpers ------------------------------------------------------
#
# Everything here comes off the wire, so every one of these returns None
# rather than raising. A malformed kilometre reading must not cost us the
# status update it arrived with.


def parse_timestamp(value):
    from django.utils.dateparse import parse_datetime

    if not value:
        return None
    parsed = parse_datetime(str(value))
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        # A phone that reports local time with no offset. Assume ours --
        # both ends of this integration are in one country.
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def parse_date_only(value):
    from django.utils.dateparse import parse_date

    if not value:
        return None
    return parse_date(str(value)[:10])


def to_decimal(value):
    from decimal import Decimal, InvalidOperation

    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def to_int(value):
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


HasSchedulingAccess = section_permission("scheduling")

#: How far back a position is worth drawing at all. Somebody whose last
#: report was yesterday afternoon is not anywhere useful on today's map --
#: the pin would look exactly like a current one, distinguished only by a
#: timestamp nobody reads twice.
POSITION_WINDOW_HOURS = 12

#: How long a ping stays "live". Conecto pings once a minute while a
#: technician is en route, so silence longer than this means the leg ended,
#: the phone lost signal, or the app was closed -- and the pin should stop
#: claiming to be current before anybody drives somewhere on the strength
#: of it. Three missed pings, not one: a single dropped packet on a rural
#: tower should not make a moving technician look stalled.
LIVE_WITHIN_MINUTES = 4

#: How much of the drive to draw behind the pin. Long enough to show the
#: route to a callout, short enough that yesterday's trip is not still on
#: the screen.
TRAIL_MINUTES = 120

#: A ceiling so one phone stuck in a reporting loop cannot make this
#: endpoint slow for everybody.
MAX_POINTS = 5000


def _minutes_since(moment):
    if moment is None:
        return None
    return int((timezone.now() - moment).total_seconds() // 60)


def _position_row(code, user, *, ping=None, event=None, trail=()):
    """One technician, as the map and the list below it read them.

    `ping` is a periodic GPS report; `event` is the last status change.
    They answer different questions and the row carries both: the LOCATION
    comes from whichever is newer, while the STATUS only ever comes from
    an event, because a ping does not carry one.

    `source` is not a debugging field. "Live" and "last seen" are
    different claims, and the screen is only able to distinguish them
    because this says which kind of reading the pin came from.
    """
    name = ""
    if user is not None:
        name = (user.first_name or "").strip() or user.username

    ping_at = ping.recorded_at if ping is not None else None
    event_at = event.occurred_at if event is not None else None

    source = ""
    lat = lng = None
    occurred_at = None
    if ping is not None and (event_at is None or (ping_at is not None and ping_at >= event_at)):
        source = "ping"
        lat, lng = float(ping.lat), float(ping.lng)
        occurred_at = ping_at
    elif event is not None and event.lat is not None and event.lng is not None:
        source = "status"
        lat, lng = float(event.lat), float(event.lng)
        occurred_at = event_at

    minutes_ago = _minutes_since(occurred_at)

    # A job for the pin: the one the ping named, else the one the last
    # status change was about. A ping between jobs carries neither, and
    # the row simply has no job -- which is the truth.
    job = None
    if ping is not None and ping.job_id:
        job = ping.job
    elif event is not None and event.job_id:
        job = event.job

    return {
        "code": code or (user.staff_code if user is not None else ""),
        "name": name or code or "Unknown",
        "status": event.status if event is not None else "",
        "lat": lat,
        "lng": lng,
        "occurred_at": occurred_at.isoformat() if occurred_at is not None else None,
        "minutes_ago": minutes_ago,
        "source": source,
        # The claim the screen is allowed to make. Deliberately decided
        # here rather than in the browser, so a stale tab cannot keep
        # calling an old pin live by doing its own arithmetic.
        "live": bool(
            source == "ping" and minutes_ago is not None and minutes_ago <= LIVE_WITHIN_MINUTES
        ),
        "job_id": job.pk if job is not None else None,
        "job_title": job.title if job is not None else "",
        "customer_name": (
            job.customer.full_name if job is not None and job.customer_id else ""
        ),
        # Oldest first, so a polyline drawn straight from it runs the way
        # the technician drove.
        "trail": list(trail),
    }


class TechnicianPositionsView(APIView):
    """Where each technician is, and how much that word is worth.

    Conecto's app pings a position once a minute BETWEEN THE START TAP AND
    THE CHECK-IN TAP, and at no other time (confirmed 2026-09-21, after an
    earlier answer that there was no periodic feed at all). So this
    endpoint returns two quite different kinds of pin and says which is
    which:

      source="ping"   a live position from a technician on the road. With
                      live=true it is at most a few minutes old.
      source="status" the last place a status change was reported from --
                      usually the customer's own address at check-in, and
                      possibly hours ago.

    Collapsing those two into "position" is the failure mode this whole
    endpoint is shaped against. The pin looks identical either way, and
    somebody deciding whether to send a second technician to a callout is
    reading a pin that is either sixty seconds old or from before lunch.

    Technicians who have reported nothing at all are still returned, with
    nulls, for the same reason as before: a map that silently omits them
    reads as "nothing to report" rather than "we do not know where this
    person is", and those are different sentences.
    """

    permission_classes = [permissions.IsAuthenticated, IsStaffMember, HasSchedulingAccess]

    def get(self, request):
        from django.contrib.auth import get_user_model

        from .models import ConectoJobEvent, ConectoTechnician, ConectoTechnicianPosition

        User = get_user_model()
        now = timezone.now()
        since = now - timedelta(hours=POSITION_WINDOW_HOURS)
        trail_since = now - timedelta(minutes=TRAIL_MINUTES)

        technicians = list(
            User.objects.filter(role=User.Role.TECHNICIAN, is_active=True)
            .order_by("first_name", "username")
        )

        # A technician is known to Conecto by a staff code, or by a
        # hand-set technician code, or historically by both. A report
        # carries whichever one that phone registered under, so the lookup
        # has to work from either.
        code_to_user = {}
        for user in technicians:
            if user.staff_code:
                code_to_user[user.staff_code] = user
        for link in ConectoTechnician.objects.select_related("user"):
            if link.technician_code:
                code_to_user[link.technician_code] = link.user

        latest_ping = {}
        trails = {}
        pings = (
            ConectoTechnicianPosition.objects
            .filter(recorded_at__gte=since)
            .exclude(technician_code="")
            .select_related("job", "job__customer")
            .order_by("-recorded_at", "-id")[:MAX_POINTS]
        )
        for ping in pings:
            # Newest first, so the first one seen for a code is the one to
            # keep. Cheaper than a window function and easier to read.
            latest_ping.setdefault(ping.technician_code, ping)
            if ping.recorded_at >= trail_since:
                trails.setdefault(ping.technician_code, []).append(
                    [float(ping.lat), float(ping.lng)]
                )

        latest_event = {}
        events = (
            ConectoJobEvent.objects
            .filter(occurred_at__gte=since)
            .exclude(technician_code="")
            .select_related("job", "job__customer")
            .order_by("-occurred_at", "-id")
        )
        for event in events:
            latest_event.setdefault(event.technician_code, event)

        rows = []
        seen_users = set()
        for code in set(latest_ping) | set(latest_event):
            user = code_to_user.get(code)
            if user is not None:
                seen_users.add(user.pk)
            rows.append(
                _position_row(
                    code, user,
                    ping=latest_ping.get(code),
                    event=latest_event.get(code),
                    # Reversed: collected newest-first above, drawn
                    # oldest-first so the line runs the way they drove.
                    trail=list(reversed(trails.get(code, []))),
                )
            )

        for user in technicians:
            if user.pk not in seen_users:
                rows.append(_position_row(user.staff_code or "", user))

        # Live first, then anybody else with a position, then the
        # unaccounted-for -- each group by name. Somebody on the road is
        # the row being looked for, and it belongs at the top rather than
        # alphabetically among people who finished hours ago.
        rows.sort(
            key=lambda row: (
                not row["live"], row["minutes_ago"] is None, row["name"].lower(),
            )
        )
        # SHAPED LIKE EVERY OTHER LIST IN THE PLATFORM, and that is not
        # cosmetic. This is an APIView rather than a ViewSet, so DRF's
        # pagination never wrapped it, and it returned a bare array while
        # every list endpoint beside it returns {count, results}.
        #
        # useApiList reads res.data.results. Handed an array it set items
        # to undefined, and the first items.filter() on the page threw --
        # which React answers by unmounting the tree, so the screen went
        # white with nothing in the network tab to suggest why. The map
        # shipped broken and the build was green throughout, because the
        # hook is typed Paginated<T> and TypeScript believed it.
        return Response({"count": len(rows), "results": rows})
