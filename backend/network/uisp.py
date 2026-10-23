"""Read-only view of Skybre's UISP (Rudolph, 14 Oct 2026: "UISP is our next
build"). Backhaul, access points and core devices, outages and the site
tree, for the Network (UISP) page and the Dashboard tiles.

Read from UISP's NMS API v2.1 with the token of UISP's "Vexar" user
(settings.UISP_URL / UISP_TOKEN / UISP_VERIFY_TLS). Nothing is written to
UISP. Answers are cached for a minute per worker, so a page and its
Dashboard tiles don't each hit UISP.

What the fields mean, checked against Skybre's own UISP on 14 Oct:
* overview.linkScore.linkScore is UISP's "Link potential" (BB-SPA 0.335 = 33%).
* overview.outageScore is the share of time down -- what UISP's list shows
  as "Uptime" (ORX-SS 0.005 = "1%"). Shown here as Downtime %.
* overview.wirelessMode "ap-ptp" / "sta-ptp" = a backhaul radio; "ap-ptmp" /
  "ap" = an access point; "sta" = a station.
* MikroTiks are "blackBox" devices: UISP only knows whether they answer.
"""
import logging
import re
import warnings

import requests
import urllib3
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

CACHE_SECONDS = 60
TIMEOUT = 15
LOW_LINK = 0.5        # link potential under 50% is flagged
BUSY = 0.6            # downlink use at 60%+ is flagged


class UispError(Exception):
    pass


def configured():
    return bool(getattr(settings, "UISP_URL", "") and getattr(settings, "UISP_TOKEN", ""))


def _get(path, params=None):
    if not configured():
        raise UispError("UISP isn't connected yet: set UISP_URL and UISP_TOKEN in backend/.env.")
    url = f"{settings.UISP_URL}/nms/api/v2.1{path}"
    verify = getattr(settings, "UISP_VERIFY_TLS", False)
    try:
        with warnings.catch_warnings():
            # A self-signed UISP would log an InsecureRequestWarning on every
            # call; skipping verification is the deliberate setting.
            if not verify:
                warnings.simplefilter("ignore", urllib3.exceptions.InsecureRequestWarning)
            response = requests.get(
                url, params=params or {}, timeout=TIMEOUT,
                headers={"x-auth-token": settings.UISP_TOKEN, "accept": "application/json"},
                verify=verify,
            )
    except requests.RequestException as exc:
        raise UispError(f"Couldn't reach UISP at {settings.UISP_URL}: {exc.__class__.__name__}") from exc
    if response.status_code == 401:
        raise UispError("UISP refused the token (401). Check UISP_TOKEN.")
    if response.status_code != 200:
        raise UispError(f"UISP answered HTTP {response.status_code} for {path}.")
    try:
        return response.json()
    except ValueError as exc:
        raise UispError("UISP sent something that isn't JSON.") from exc


def _cached(key, fetch, seconds=CACHE_SECONDS):
    data = cache.get(key)
    if data is None:
        data = fetch()
        cache.set(key, data, seconds)
    return data


# --- devices ------------------------------------------------------------------


def kind_of(raw):
    ident = raw.get("identification") or {}
    mode = ((raw.get("overview") or {}).get("wirelessMode") or "").lower()
    if mode.endswith("ptp"):
        return "ptp"
    if mode in ("ap", "ap-ptmp") or (ident.get("role") == "ap" and not mode):
        return "ap"
    if mode == "sta" or ident.get("role") == "station":
        return "station"
    if ident.get("role") == "router":
        return "router"
    if ident.get("role") == "switch":
        return "switch"
    return "other"


def _pct(value):
    return None if value is None else round(float(value) * 100, 1)


def device_row(raw):
    ident = raw.get("identification") or {}
    ov = raw.get("overview") or {}
    meta = raw.get("meta") or {}
    site = ident.get("site") or {}
    link = (ov.get("linkScore") or {}).get("linkScore")
    status = ov.get("status") or "unknown"
    row = {
        "id": ident.get("id"),
        "name": ident.get("displayName") or ident.get("name") or "",
        "model": ident.get("modelName") or ident.get("model") or "",
        "type": ident.get("type") or "",
        "kind": kind_of(raw),
        "black_box": ident.get("type") == "blackBox",
        "status": status,
        "online": status == "active",
        "maintenance": bool(meta.get("maintenance")),
        "ip": (raw.get("ipAddress") or "").split("/")[0],
        "mac": ident.get("mac") or "",
        "site": {"id": site.get("id"), "name": (site.get("name") or "").strip()} if site.get("id") else None,
        "link_potential_pct": _pct(link),
        "downtime_pct": _pct(ov.get("outageScore")),
        "signal_dbm": ov.get("signal"),
        "remote_signal_dbm": ov.get("remoteSignalMax"),
        "downlink_capacity_bps": ov.get("downlinkCapacity"),
        "uplink_capacity_bps": ov.get("uplinkCapacity"),
        "downlink_use_pct": _pct(ov.get("downlinkUtilization")),
        "uplink_use_pct": _pct(ov.get("uplinkUtilization")),
        "downlink_bps": (ov.get("downlinkCapacity") or 0) * (ov.get("downlinkUtilization") or 0) or None,
        "uplink_bps": (ov.get("uplinkCapacity") or 0) * (ov.get("uplinkUtilization") or 0) or None,
        "cpu_pct": ov.get("cpu"),
        "ram_pct": ov.get("ram"),
        "uptime_s": ov.get("uptime"),
        "frequency_mhz": ov.get("frequency"),
        "channel_width_mhz": ov.get("channelWidth"),
        "distance_m": ov.get("distance"),
        "temperature_c": ov.get("temperature"),
        "stations": ov.get("stationsCount"),
        "last_seen": ov.get("lastSeen"),
        "firmware": ident.get("firmwareVersion") or "",
        "update_available": bool(ov.get("canUpgrade")),
        "ssid": (raw.get("attributes") or {}).get("ssid") or "",
        "wireless_mode": ov.get("wirelessMode") or "",
        # The AP this radio (a station) connects to -- the links drawn on
        # the Topology map (#172).
        "ap_device_id": ((raw.get("attributes") or {}).get("apDevice") or {}).get("id"),
        "ap_site_id": ((raw.get("attributes") or {}).get("apDevice") or {}).get("siteId"),
    }
    flags = []
    if not row["online"]:
        flags.append("offline")
    if link is not None and link < LOW_LINK:
        flags.append("low_link")
    if (ov.get("downlinkUtilization") or 0) >= BUSY:
        flags.append("busy")
    if row["update_available"]:
        flags.append("update")
    if row["maintenance"]:
        flags.append("maintenance")
    row["flags"] = flags
    return row


def devices():
    return _cached("uisp:devices", lambda: [device_row(d) for d in _get("/devices")])


# --- sites ----------------------------------------------------------------------


def sites():
    def fetch():
        out = []
        for raw in _get("/sites"):
            ident = raw.get("identification") or {}
            desc = raw.get("description") or {}
            if ident.get("type") != "site":
                continue
            loc = desc.get("location") or {}
            out.append({
                "id": raw.get("id"),
                "name": (ident.get("name") or "").strip(),
                "parent_id": (ident.get("parent") or {}).get("id"),
                "status": ident.get("status") or "",
                "device_count": desc.get("deviceCount") or 0,
                "device_outage_count": desc.get("deviceOutageCount") or 0,
                "height": desc.get("height"),   # antenna height on the site, m (line of sight, #179)
                "latitude": loc.get("latitude"),
                "longitude": loc.get("longitude"),
            })
        return out

    return _cached("uisp:sites", fetch)


# --- outages -------------------------------------------------------------------


def outages(count=50, page=1):
    def fetch():
        data = _get("/outages", {"count": count, "page": page})
        items = data.get("items", []) if isinstance(data, dict) else data
        out = []
        for raw in items:
            dev = raw.get("device") or {}
            site = dev.get("site") or {}
            out.append({
                "id": raw.get("id"),
                "type": raw.get("type") or "",
                "in_progress": bool(raw.get("inProgress")),
                "start": raw.get("startTimestamp"),
                "end": raw.get("endTimestamp"),
                "duration_s": round((raw.get("aggregatedTime") or 0) / 1000),
                "device": {"id": dev.get("id"), "name": dev.get("displayName") or dev.get("name") or "",
                           "model": dev.get("modelName") or ""},
                "site": (site.get("name") or "").strip(),
            })
        total = (data.get("pagination") or {}).get("total") if isinstance(data, dict) else None
        return {"items": out, "total": total}

    return _cached(f"uisp:outages:{count}:{page}", fetch)


# --- graphs (#170) ------------------------------------------------------------------
#
# /devices/{id}/statistics?interval=... as UISP keeps it (checked 14 Oct on
# ORX-SS and an AP): every series is {"avg": [{x: ms, y}], "max": [...]}; day =
# 6-minute points, week = hourly, month = 2-hourly. Throughput is per
# interface (bits/s); "main" is the radio. UISP's downlink is the AP -> station
# direction of the link, so on the AP side it is the radio's transmit and on
# the station side its receive (ORX-SS and SS-ORX report the same downlink).
# UISP keeps no latency for these radios (ping is empty), so there is no
# latency graph.

INTERVALS = {"hour": 60, "day": 60, "week": 300, "month": 300}   # -> seconds cached
_PCT = {"downlinkUtilization", "uplinkUtilization", "linkScore", "downlinkScore", "uplinkScore", "airTime"}
_PLAIN = {"downlinkCapacity", "uplinkCapacity", "signal", "remoteSignal", "localChain0", "localChain1",
          "remoteChain0", "remoteChain1", "clients", "cpu", "ram"}


def _radio_interface(interfaces):
    by_id = {i.get("id"): i for i in interfaces}
    if "main" in by_id:
        return by_id["main"]
    for itf in interfaces:
        if "ghz" in (itf.get("name") or "").lower() or (itf.get("id") or "").startswith(("ath", "wlan")):
            return itf
    for key in ("data", "eth0"):
        if key in by_id:
            return by_id[key]
    return interfaces[0] if interfaces else None


def statistics(device_id, interval="day"):
    if interval not in INTERVALS:
        raise UispError(f"Unknown range {interval!r}.")
    if not re.fullmatch(r"[0-9A-Fa-f-]{8,64}", device_id or ""):
        raise UispError("Unknown device.")

    def fetch():
        mode = next((d["wireless_mode"] for d in devices() if d["id"] == device_id), "")
        raw = _get(f"/devices/{device_id}/statistics", {"interval": interval})
        rows = {}

        def put(key, points, scale=1):
            for p in points or []:
                x, y = p.get("x"), p.get("y")
                if x is None or y is None:
                    continue
                rows.setdefault(x, {"t": x})[key] = round(float(y) * scale, 3)

        for key in _PCT | _PLAIN:
            put(key, ((raw.get(key) or {}).get("avg")), 100 if key in _PCT else 1)
        radio = _radio_interface(raw.get("interfaces") or [])
        if radio:
            tx = (radio.get("transmit") or {}).get("avg")
            rx = (radio.get("receive") or {}).get("avg")
            station_side = mode.startswith("sta")
            put("down_bps", rx if station_side else tx)
            put("up_bps", tx if station_side else rx)
        span = raw.get("interval") or {}
        end = span.get("end")
        # A few series carry one extra, still-filling point after the range
        # end (all zeros) -- left out so "Last" isn't a false 0.
        points = [rows[x] for x in sorted(rows) if end is None or x <= end]
        present = sorted({k for r in points for k in r if k != "t"})
        return {
            "interval": interval,
            "period_s": round((raw.get("period") or 0) / 1000),
            "start": span.get("start"),
            "end": span.get("end"),
            "interface": (radio or {}).get("name") or "",
            "wireless_mode": mode,
            "series": present,
            "points": points,
        }

    return _cached(f"uisp:stats:{device_id}:{interval}", fetch, INTERVALS[interval])


# --- the site tree with this platform's clients ----------------------------------


def clients_per_site_name():
    """{lower-case site name: active lines} -- a UISP site matched to a
    Networking site of the same name, counting lines whose access device is
    at that site."""
    from django.db.models import Count

    from billing.models import Service

    rows = (
        Service.objects.filter(status="active", access_device__site__isnull=False)
        .values("access_device__site__title")
        .annotate(n=Count("id"))
    )
    return {(r["access_device__site__title"] or "").strip().lower(): r["n"] for r in rows}


def summary():
    rows = devices()
    ptp = [r for r in rows if r["kind"] == "ptp"]
    aps = [r for r in rows if r["kind"] == "ap"]
    out = outages(count=50)
    open_now = [o for o in out["items"] if o["in_progress"]]
    last = next((o for o in out["items"] if not o["in_progress"]), None)
    return {
        "devices": len(rows),
        "devices_online": sum(1 for r in rows if r["online"]),
        "ptp_radios": len(ptp),
        "ptp_offline": sum(1 for r in ptp if not r["online"]),
        "aps": len(aps),
        "aps_offline": sum(1 for r in aps if not r["online"]),
        "low_link": sum(1 for r in rows if "low_link" in r["flags"]),
        # Per Network (UISP) tab, so the dashboard's Low link potential tile
        # (15 Oct 2026) opens the tab where most of them are.
        "low_link_tabs": {
            tab: sum(1 for r in rows if "low_link" in r["flags"] and (r["kind"] in kinds))
            for tab, kinds in (("ptp", ("ptp",)), ("ap", ("ap",)), ("other", ("router", "switch", "station", "other")))
        },
        "busy": sum(1 for r in rows if "busy" in r["flags"]),
        "updates": sum(1 for r in rows if "update" in r["flags"]),
        "maintenance": sum(1 for r in rows if r["maintenance"]),
        "outages_open": open_now,
        "last_outage": last,
        "outages_total": out["total"],
    }
