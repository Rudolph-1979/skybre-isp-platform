"""Networking -> Map (#173, Rudolph 14 Oct 2026: "One map that services
everything"): address search and the serviceability check.

Address search uses OpenStreetMap's Nominatim (free, no key). Its rules: at
most one request a second, an identifying User-Agent, no search-as-you-type
-- so the page searches on Enter only, answers are cached for a day and
calls are spaced a second apart. Farm roads and plots are often missing from
OpenStreetMap; staff then paste GPS or click the map instead. Swapping to
Google later means replacing geocode() only.

Serviceability: the nearest UISP towers to a point (distance and bearing),
with the access points at each. Coverage areas (KMZ import) come later;
until then "covered" is None and the result says coverage isn't loaded.
"""
import logging
import math
import time

import requests
from django.conf import settings
from django.core.cache import cache

from . import coverage, uisp

logger = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "SkybrePlatform/1.0 (+https://portal.skybre.co.za)"
GEOCODE_CACHE_SECONDS = 24 * 3600
TIMEOUT = 10
NEAREST = 5


class GeoError(Exception):
    pass


def _space_requests():
    """Keep at least a second between calls to Nominatim (best effort,
    shared through the cache by all workers)."""
    last = cache.get("geo:nominatim:last")
    if last is not None:
        wait = 1.05 - (time.time() - last)
        if wait > 0:
            time.sleep(min(wait, 1.05))
    cache.set("geo:nominatim:last", time.time(), 10)


def geocode(query):
    query = " ".join((query or "").split())
    if len(query) < 3:
        raise GeoError("Type at least 3 characters.")
    key = "geo:q:" + query.lower()[:200]
    hit = cache.get(key)
    if hit is not None:
        return hit
    _space_requests()
    try:
        res = requests.get(
            getattr(settings, "GEOCODER_URL", "") or NOMINATIM_URL,
            params={"q": query, "format": "jsonv2", "countrycodes": "za", "limit": 5},
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en"},
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        raise GeoError("Couldn't reach the address search (OpenStreetMap).") from exc
    if res.status_code != 200:
        raise GeoError(f"Address search answered HTTP {res.status_code}.")
    try:
        rows = res.json()
    except ValueError as exc:
        raise GeoError("Address search sent something that isn't JSON.") from exc
    out = []
    for r in rows if isinstance(rows, list) else []:
        try:
            out.append({"label": r.get("display_name") or "", "lat": round(float(r["lat"]), 6),
                        "lng": round(float(r["lon"]), 6), "kind": r.get("type") or ""})
        except (KeyError, TypeError, ValueError):
            continue
    cache.set(key, out, GEOCODE_CACHE_SECONDS)
    return out


# --- serviceability -------------------------------------------------------------

def _distance_km(lat1, lng1, lat2, lng2):
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _bearing(lat1, lng1, lat2, lng2):
    """Degrees from north, tower -> point (which way a sector must face)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lng2 - lng1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return round((math.degrees(math.atan2(x, y)) + 360) % 360)


def _compass(deg):
    return ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][int((deg + 22.5) // 45) % 8]


def check_point(lat, lng, client_height=None):
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise GeoError("Those coordinates aren't on Earth.")
    towers = []
    if uisp.configured():
        devices = uisp.devices()
        sites = uisp.sites()
        # Stand-alone UISP sites (no parent, nothing under them) are client
        # installs, not towers -- UISP's own map leaves them off too.
        parents = {s["parent_id"] for s in sites if s["parent_id"]}
        for s in sites:
            if s["latitude"] is None or s["longitude"] is None:
                continue
            if not s["parent_id"] and s["id"] not in parents:
                continue
            here = [d for d in devices if d["site"] and d["site"]["id"] == s["id"]]
            # Sectors: UISP access points, plus MikroTik sectors, which UISP
            # only knows as black boxes -- recognised by "sect" in the name
            # (WTH_SECT_01, ORX Sect 02 ...).
            aps = [d for d in here if d["kind"] == "ap" or (d["black_box"] and "sect" in d["name"].lower())]
            bearing = _bearing(s["latitude"], s["longitude"], lat, lng)
            towers.append({
                "id": s["id"],
                "name": s["name"],
                "latitude": s["latitude"],
                "longitude": s["longitude"],
                "distance_km": round(_distance_km(lat, lng, s["latitude"], s["longitude"]), 2),
                "height_m": s.get("height"),
                "bearing": bearing,
                "compass": _compass(bearing),
                "devices": len(here),
                "devices_offline": sum(1 for d in here if not d["online"]),
                "access_points": [
                    {"id": d["id"], "name": d["name"], "model": d["model"], "online": d["online"],
                     "frequency_mhz": d["frequency_mhz"], "ssid": d.get("ssid") or "",
                     "mikrotik": d["black_box"]}
                    for d in sorted(aps, key=lambda d: d["name"].lower())
                ],
            })
        towers.sort(key=lambda t: t["distance_km"])
        # Line of sight over terrain from the 3 nearest (#179).
        from . import terrain

        client_h = client_height or terrain.DEFAULT_CLIENT_HEIGHT
        for t in towers[:3]:
            freqs = sorted(a["frequency_mhz"] for a in t["access_points"] if a.get("frequency_mhz"))
            t["los"] = terrain.line_of_sight(
                t["latitude"], t["longitude"], float(t["height_m"] or 10), lat, lng,
                client_height=client_h, freq_mhz=freqs[len(freqs) // 2] if freqs else None,
            )
    return {
        "point": {"lat": lat, "lng": lng},
        # Imported KML/KMZ coverage (#178): areas the point is inside and the
        # nearest serviceable house dots.
        "coverage": coverage.check(lat, lng),
        "uisp_connected": uisp.configured(),
        "nearest": towers[:NEAREST],
        "client_height_m": client_height or None,
        "terrain_loaded": _terrain_loaded(),
    }


def _terrain_loaded():
    from . import terrain

    return terrain.available()
