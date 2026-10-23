"""Client CPE signal, read from the MikroTik APs (Rudolph, 8 Oct 2026).

How a service is matched to a row in an AP's wireless registration table:
the client's CPE dials PPPoE itself (router mode), so FreeRADIUS records
its MAC as the session's Calling-Station-Id. Confirmed on Skybre's network:
krd_gaggle_inn's session MAC B8:69:F4:61:3C:BB is the "Gaggle In" row on
the AP at 10.200.6.7. Nothing has to be typed in.

Which devices are read: every Device with the RouterOS API switched on.
One without wireless simply has no registration table and is skipped --
that costs one API round trip, and saves depending on every AP having been
given the right device type. Ubiquiti APs are not read (Skybre doesn't run
clients through UISP); a CPE on one shows as "not seen".

Field names are RouterOS's: the legacy wireless package
(/interface/wireless/registration-table) first, then the newer wifi /
wifiwave2 packages. Values arrive as strings -- "-49", "-50@HT20-7",
"73" -- and are parsed defensively; anything unparseable is left blank
rather than guessed.
"""
import logging
import re

from django.db import transaction
from django.utils import timezone

from .models import CpeSignal, CpeSignalSample, Device

logger = logging.getLogger(__name__)

KEEP_SAMPLES_DAYS = 7
TABLES = (
    "/interface/wireless/registration-table/print",
    "/interface/wifi/registration-table/print",
    "/interface/wifiwave2/registration-table/print",
)
_DBM = re.compile(r"(-?\d+)")
_MAC = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$")


def norm_mac(value):
    text = (value or "").strip().upper().replace("-", ":")
    if re.fullmatch(r"[0-9A-F]{12}", text):
        text = ":".join(text[i:i + 2] for i in range(0, 12, 2))
    return text if _MAC.match(text) else ""


def _int(value):
    if value is None:
        return None
    match = _DBM.search(str(value))
    return int(match.group(1)) if match else None


def parse_row(row, device):
    """One registration-table row -> our fields. `row` is RouterOS's dict."""
    signal = row.get("signal-strength", row.get("signal"))
    return {
        "mac": norm_mac(row.get("mac-address")),
        "ap": device,
        "interface": str(row.get("interface") or "")[:64],
        "radio_name": str(row.get("radio-name") or "")[:64],
        "signal_dbm": _int(signal),
        "tx_signal_dbm": _int(row.get("tx-signal-strength")),
        "snr_db": _int(row.get("signal-to-noise")),
        "tx_ccq": _int(row.get("tx-ccq")),
        "rx_ccq": _int(row.get("rx-ccq")),
        "tx_rate": str(row.get("tx-rate") or "")[:64],
        "rx_rate": str(row.get("rx-rate") or "")[:64],
        "uptime": str(row.get("uptime") or "")[:32],
        "distance_km": str(row.get("distance") or "")[:10],
    }


def read_registration(device):
    """Rows from whichever wireless package the device runs; [] if none.
    Raises network.mikrotik.MikrotikError when the device can't be reached."""
    from . import mikrotik

    with mikrotik.api_connection(device, timeout=6) as api:
        for path in TABLES:
            try:
                rows = list(api(path))
            except Exception:  # noqa: BLE001 -- "no such command": this package isn't installed
                continue
            return [parse_row(r, device) for r in rows]
    return []


def scan(devices=None):
    """{mac: fields} across the APs, plus the devices that failed."""
    seen, failed = {}, []
    devices = devices if devices is not None else Device.objects.filter(api_enabled=True)
    for device in devices:
        try:
            rows = read_registration(device)
        except Exception as exc:  # noqa: BLE001 -- one dead AP must not stop the rest
            failed.append((device, str(exc)))
            continue
        for row in rows:
            if row["mac"]:
                seen[row["mac"]] = row
    return seen, failed


def service_macs(services):
    """{service_id: MAC} from each line's newest RADIUS session."""
    from radiusauth.models import RadAcct

    by_login = {}
    for s in services:
        login = (s.radius_username or "").strip()
        if login:
            by_login.setdefault(login, []).append(s.pk)
    macs = {}
    if not by_login:
        return macs
    rows = (
        RadAcct.objects.filter(username__in=list(by_login))
        .exclude(callingstationid__isnull=True).exclude(callingstationid="")
        .order_by("username", "-acctstarttime")
        .values_list("username", "callingstationid")
    )
    done = set()
    for login, mac in rows:
        if login in done:
            continue
        done.add(login)
        mac = norm_mac(mac)
        if mac:
            for pk in by_login[login]:
                macs[pk] = mac
    return macs


def store(services, seen, now=None, sample=True):
    """Write each service's current state (and a sample) from a scan."""
    now = now or timezone.now()
    macs = service_macs(services)
    samples = []
    with transaction.atomic():
        for s in services:
            mac = macs.get(s.pk, "")
            row = seen.get(mac) if mac else None
            current, _ = CpeSignal.objects.get_or_create(service=s)
            current.mac = mac
            current.checked_at = now
            if row is None:
                current.state = CpeSignal.State.NOT_SEEN if mac else CpeSignal.State.NO_MAC
                # The last reading is kept (with seen_at) so "last seen
                # -61 dBm on SS-VF, 2 h ago" is still on screen.
                current.save()
                continue
            current.state = CpeSignal.State.OK
            current.seen_at = now
            for key, value in row.items():
                if key != "mac":
                    setattr(current, key, value)
            current.save()
            if sample:
                samples.append(CpeSignalSample(
                    service=s, at=now, signal_dbm=row["signal_dbm"], tx_signal_dbm=row["tx_signal_dbm"],
                    tx_ccq=row["tx_ccq"], rx_ccq=row["rx_ccq"],
                ))
        CpeSignalSample.objects.bulk_create(samples)
    return len(samples)


def active_services():
    from billing.models import Service

    return list(
        Service.objects.filter(status=Service.Status.ACTIVE)
        .exclude(radius_username="").exclude(radius_username__isnull=True)
    )


def poll_all():
    """The scheduled run: every AP, every active line. Returns a summary."""
    seen, failed = scan()
    services = active_services()
    stored = store(services, seen)
    CpeSignalSample.objects.filter(
        at__lt=timezone.now() - timezone.timedelta(days=KEEP_SAMPLES_DAYS)
    ).delete()
    for device, error in failed:
        logger.warning("CPE signal: %s not read: %s", device, error)
    return {"stations": len(seen), "services": len(services), "matched": stored, "failed": len(failed)}


def refresh_service(service):
    """Read now, for one line (the Refresh button): the AP it was last seen
    on first; if it isn't there any more, every AP."""
    current = CpeSignal.objects.filter(service=service).select_related("ap").first()
    if current is not None and current.ap_id and current.ap.api_enabled:
        seen, _ = scan([current.ap])
        mac = service_macs([service]).get(service.pk)
        if mac and mac in seen:
            store([service], seen, sample=False)
            return CpeSignal.objects.get(service=service)
    seen, _ = scan()
    store([service], seen, sample=False)
    return CpeSignal.objects.get(service=service)
