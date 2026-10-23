"""Digital job cards from the Conecto app (7 Oct 2026).

POST /api/webhooks/conecto/job-card/ -- see ConectoJobCard for what is kept
and why. Every field may be null; nothing but conecto_job_card_id is
required, because a card that can't be linked to a job or client is still a
record worth keeping (it is linked later if the client code turns up).
"""
import base64
import io
import ipaddress
import logging
import socket
from urllib.parse import urlparse

from django.db import transaction
from django.utils import timezone

from .models import ConectoJobCard

logger = logging.getLogger(__name__)

MAX_SIGNATURE_BYTES = 2 * 1024 * 1024

#: Job types as Conecto sends them, and how the card prints them.
JOB_TYPES = {
    "callout": "Call-out", "ap": "AP", "panning": "Panning", "maintenance": "Maintenance",
    "callback": "Call-back", "repair": "Repair", "install": "Installation",
}

#: The card's sections, in the paper card's order: (heading, [(key, label)]).
SECTIONS = [
    ("Client", [
        ("client_name", "Client"), ("client_code", "Client code"), ("reference_number", "Reference"),
        ("address", "Address"), ("town", "Town"), ("contact_number", "Contact number"), ("email", "Email"),
    ]),
    ("Package", [
        ("package_mbps", "Package (Mbps)"), ("package_price", "Price"),
        ("package_with_insurance", "Price with insurance"), ("insurance_wanted", "Insurance wanted"),
    ]),
    ("Equipment", [
        ("equipment_ownership", "Ownership"), ("equipment_condition", "Condition"),
        ("antenna_brand", "Antenna"), ("antenna_model", "Antenna model"),
        ("router_brand", "Router"), ("router_model", "Router model"),
        ("bracket_size", "Bracket"), ("pole_m", "Pole (m)"),
        ("cable_cat5", "CAT5 cable"), ("cable_indoor", "Indoor cable"), ("cable_outdoor", "Outdoor cable"),
        ("cable_m", "Cable (m)"),
    ]),
    ("Materials", [
        ("rj45", "RJ45"), ("c_screw_plugs", "C-screws & plugs"), ("u_bolts", "U-bolts"),
        ("stay_wire", "Stay wire"), ("glue_tube", "Glue tube"),
    ]),
    ("Connection and tower", [
        ("speed_down_mbps", "Speed down (Mbps)"), ("speed_up_mbps", "Speed up (Mbps)"),
        ("devices_checked", "Devices checked"), ("tower_name", "Tower"), ("signal", "Signal"),
        ("line_of_sight", "Line of sight"),
    ]),
    ("Notes", [
        ("job_details", "Job details"), ("install_los_info", "Install / LOS info"), ("client_note", "Client note"),
    ]),
    ("Additional work", [
        ("additional_work_needed", "Needed"), ("additional_work_description", "Description"),
        ("additional_work_ticket_id", "Ticket"),
    ]),
    ("Times", [
        ("checked_in_at", "Checked in"), ("completed_at", "Completed"), ("duration_minutes", "Duration (min)"),
        ("km_traveled", "Km travelled"), ("submitted_at", "Submitted"),
    ]),
]

_CHOICE_LABELS = {
    "own": "Own", "loan": "Loan", "new": "New", "second_hand": "Second hand",
    "mikrotik": "MikroTik", "ubiquiti": "Ubiquiti", "tenda": "Tenda", "cudy": "Cudy", "other": "Other",
    "small": "Small", "medium": "Medium", "large": "Large", "custom": "Custom",
    "los": "Yes (LOS)", "no_los": "No LOS", "yes": "Yes", "no": "No",
}


def _text(data, key, limit=255):
    value = data.get(key)
    return "" if value is None else str(value).strip()[:limit]


def _when(data, key):
    from .views import parse_timestamp

    return parse_timestamp(data.get(key))


def _link_job(data):
    from scheduling.models import Job

    from .models import ConectoJob

    vexar = _text(data, "vexar_job_id", 64)
    if vexar.isdigit():
        job = Job.objects.filter(pk=int(vexar)).first()
        if job is not None:
            return job
    conecto_job = _text(data, "conecto_job_id", 128)
    if conecto_job:
        link = ConectoJob.objects.select_related("job").filter(conecto_job_id=conecto_job).first()
        if link is not None:
            return link.job
    return None


def record_job_card(data):
    """(card, created). Raises ValueError for a body without an id."""
    from customers.models import Customer
    from fleet.conecto_logs import find_staff

    card_id = _text(data, "conecto_job_card_id", 128)
    if not card_id:
        raise ValueError("conecto_job_card_id is required.")
    existing = ConectoJobCard.objects.filter(conecto_job_card_id=card_id).first()
    if existing is not None:
        return existing, False

    job = _link_job(data)
    code = _text(data, "client_code", 40)
    customer = Customer.objects.filter(customer_id__iexact=code).first() if code else None
    if customer is None and job is not None and job.customer_id:
        customer = job.customer
    types = data.get("job_types")
    types = [str(t)[:30] for t in types] if isinstance(types, list) else []
    with transaction.atomic():
        card = ConectoJobCard.objects.create(
            conecto_job_card_id=card_id,
            job=job,
            customer=customer,
            technician=find_staff(data.get("staff_code"), data.get("technician_code")),
            conecto_job_id=_text(data, "conecto_job_id", 128),
            vexar_job_id=_text(data, "vexar_job_id", 64),
            vexar_ticket_id=_text(data, "vexar_ticket_id", 64),
            client_code=code,
            client_name=_text(data, "client_name"),
            technician_name=_text(data, "technician_name") or (
                ", ".join(str(n) for n in data["technician_names"])[:255]
                if isinstance(data.get("technician_names"), list) else _text(data, "technician_names")
            ),
            job_types=types,
            line_of_sight=_text(data, "line_of_sight", 10),
            additional_work_needed=bool(data.get("additional_work_needed")),
            additional_work_ticket_id=_text(data, "additional_work_ticket_id", 128),
            client_signed_name=_text(data, "client_signed_name"),
            client_unavailable_reason=_text(data, "client_unavailable_reason"),
            signature_url=_text(data, "client_signature_url", 1000),
            checked_in_at=_when(data, "checked_in_at"),
            completed_at=_when(data, "completed_at"),
            submitted_at=_when(data, "submitted_at"),
            data=data,
        )
    if card.signature_url:
        pk = card.pk
        transaction.on_commit(lambda: fetch_signature(pk))
    return card, True


# --- the signature ------------------------------------------------------------


def _public_host(host):
    """True only when every address the host resolves to is public -- the
    URL comes from a webhook body, so it must not be able to point this
    server at itself or the office network."""
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return False
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if (address.is_private or address.is_loopback or address.is_link_local or address.is_reserved
                or address.is_multicast or address.is_unspecified):
            return False
    return bool(infos)


def fetch_signature(pk):
    """Copy the signature image into the database. Never raises; a failure
    is recorded on the card and retried by conecto_fetch_job_card_signatures."""
    import requests
    from PIL import Image

    card = ConectoJobCard.objects.filter(pk=pk).first()
    if card is None or card.signature_png or not card.signature_url:
        return False
    error = ""
    try:
        parsed = urlparse(card.signature_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("not an https link")
        if not _public_host(parsed.hostname):
            raise ValueError("the link's host isn't a public address")
        with requests.get(card.signature_url, timeout=15, stream=True, allow_redirects=False) as response:
            if response.status_code != 200:
                raise ValueError(f"HTTP {response.status_code}")
            raw = b""
            for chunk in response.iter_content(64 * 1024):
                raw += chunk
                if len(raw) > MAX_SIGNATURE_BYTES:
                    raise ValueError("larger than 2 MB")
        image = Image.open(io.BytesIO(raw))
        image.load()
        rgba = image.convert("RGBA")
        flat = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        flat.alpha_composite(rgba)
        out = io.BytesIO()
        flat.convert("RGB").save(out, format="PNG", optimize=True)
        card.signature_png = out.getvalue()
    except Exception as exc:  # noqa: BLE001
        error = str(exc)[:255] or exc.__class__.__name__
        logger.warning("Job card %s: signature not copied: %s", card.conecto_job_card_id, error)
    card.signature_error = error
    card.save(update_fields=["signature_png", "signature_error", "updated_at"])
    return not error


# --- linking later ------------------------------------------------------------


def link_completed_job(job, job_card_id):
    """job-status-changed (completed) names the card: tie them together if
    the card arrived without a job."""
    if not job_card_id or job is None:
        return
    ConectoJobCard.objects.filter(conecto_job_card_id=str(job_card_id), job__isnull=True).update(
        job=job, customer=job.customer if job.customer_id else None, updated_at=timezone.now(),
    )


# --- the PDF --------------------------------------------------------------------


def _display(key, value):
    if value is None or value == "" or value == []:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if key.endswith("_at"):
        from .views import parse_timestamp

        moment = parse_timestamp(value)
        return timezone.localtime(moment).strftime("%d/%m/%Y %H:%M") if moment else str(value)
    if key in ("package_price", "package_with_insurance"):
        try:
            return f"R {float(value):,.2f}".replace(",", " ")
        except (TypeError, ValueError):
            return str(value)
    text = str(value)
    return _CHOICE_LABELS.get(text, text)


def sections(card):
    data = card.data or {}
    out = []
    types = ", ".join(JOB_TYPES.get(t, t) for t in card.job_types)
    head = [("Job type", types), ("Technician", card.technician_name),
            ("Job", f"#{card.job_id}" if card.job_id else card.vexar_job_id or card.conecto_job_id)]
    out.append(("Job", [(label, value) for label, value in head if value]))
    ticket = ""
    if card.additional_work_needed:
        from .models import ConectoInternalTicket

        link = ConectoInternalTicket.objects.select_related("ticket").filter(
            job_card_id=card.conecto_job_card_id).first()
        ticket = link.ticket.ticket_number if link is not None else ""
    for heading, fields in SECTIONS:
        rows = [(label, ticket if key == "additional_work_ticket_id" and ticket else _display(key, data.get(key)))
                for key, label in fields]
        rows = [(label, value) for label, value in rows if value]
        if rows:
            out.append((heading, rows))
    return out


def render_pdf(card):
    from contracts.services import _pdf_context
    from notifications.pdf import _render_pdf

    ctx = _pdf_context()
    ctx.update({
        "card": card,
        "sections": sections(card),
        "signature_src": ("data:image/png;base64," + base64.b64encode(bytes(card.signature_png)).decode())
        if card.signature_png else "",
    })
    return _render_pdf("conecto/job_card_pdf.html", ctx)
