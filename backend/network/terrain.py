"""Line of sight for the wireless check (#179, Rudolph 15 Oct 2026: "Wireless
Coverage check, we need geographical info for line Of Site").

Terrain: Copernicus DEM GLO-30 (ESA, ~30 m, free, no account), fetched once
to the server by `python manage.py fetch_terrain` from the public AWS bucket
and kept as one int16 .npy per 1x1 degree tile (whole metres; read
memory-mapped, so a check touches a few KB). It is a SURFACE model: roofs and
dense trees are in it, roughly -- good for "is a hill in the way", not for
"is the neighbour's tree in the way". The 40 m at each end are left out of
the obstruction test because there the model is mostly the client's own roof
and the tower's own site.

Heights: the tower antenna is the UISP site's "height" (Rudolph: use UISP);
the client antenna defaults to 9 m and can be changed per check.

The test, per sample along the path (every ~30 m):
  sight line   straight between the two antennas
  ground       terrain + earth bulge  d1*d2 / (2 * k * R), k = 4/3
  Fresnel r1   17.32 * sqrt(d1_km * d2_km / (f_GHz * D_km)) metres
Blocked = ground above the sight line anywhere; Marginal = less than 60% of
the first Fresnel zone clear somewhere; otherwise Clear.
"""
import math
import os
from functools import lru_cache

from django.conf import settings

EARTH_R = 6371000.0
K_FACTOR = 4 / 3
DEFAULT_CLIENT_HEIGHT = 9.0
DEFAULT_FREQ_MHZ = 5500
END_SKIP_M = 40
STEP_M = 30
MAX_SAMPLES = 600
TILE_URL = "https://copernicus-dem-30m.s3.amazonaws.com/{name}/{name}.tif"


def terrain_dir():
    return getattr(settings, "TERRAIN_DIR", None) or os.path.join(settings.MEDIA_ROOT, "terrain")


def tile_name(lat_floor, lng_floor):
    ns = "N" if lat_floor >= 0 else "S"
    ew = "E" if lng_floor >= 0 else "W"
    return f"Copernicus_DSM_COG_10_{ns}{abs(lat_floor):02d}_00_{ew}{abs(lng_floor):03d}_00_DEM"


def _paths(lat_floor, lng_floor):
    base = os.path.join(terrain_dir(), tile_name(lat_floor, lng_floor))
    return base + ".npy", base + ".json"


@lru_cache(maxsize=32)
def _tile(lat_floor, lng_floor):
    """(array, west, north, dx, dy) or None when the tile isn't on the server."""
    import json

    import numpy as np

    npy, meta = _paths(lat_floor, lng_floor)
    if not (os.path.exists(npy) and os.path.exists(meta)):
        return None
    with open(meta) as fh:
        m = json.load(fh)
    return np.load(npy, mmap_mode="r"), m["west"], m["north"], m["dx"], m["dy"]


def available():
    d = terrain_dir()
    return os.path.isdir(d) and any(f.endswith(".npy") for f in os.listdir(d))


def elevation(lat, lng):
    """Ground (surface) height in metres, bilinear; None if no tile."""
    t = _tile(math.floor(lat), math.floor(lng))
    if t is None:
        return None
    arr, west, north, dx, dy = t
    fx = (lng - west) / dx - 0.5      # pixel centres
    fy = (north - lat) / dy - 0.5
    h, w = arr.shape
    x0 = min(max(int(math.floor(fx)), 0), w - 2)
    y0 = min(max(int(math.floor(fy)), 0), h - 2)
    tx = min(max(fx - x0, 0.0), 1.0)
    ty = min(max(fy - y0, 0.0), 1.0)
    a, b = float(arr[y0, x0]), float(arr[y0, x0 + 1])
    c, d = float(arr[y0 + 1, x0]), float(arr[y0 + 1, x0 + 1])
    return (a * (1 - tx) + b * tx) * (1 - ty) + (c * (1 - tx) + d * tx) * ty


def _distance_m(lat1, lng1, lat2, lng2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(a))


def line_of_sight(tower_lat, tower_lng, tower_height, client_lat, client_lng,
                  client_height=DEFAULT_CLIENT_HEIGHT, freq_mhz=None):
    """Profile and verdict from a tower antenna to a client antenna."""
    freq_mhz = freq_mhz or DEFAULT_FREQ_MHZ
    total = _distance_m(tower_lat, tower_lng, client_lat, client_lng)
    g_tower = elevation(tower_lat, tower_lng)
    g_client = elevation(client_lat, client_lng)
    if g_tower is None or g_client is None:
        return {"available": False, "reason": "Terrain for this area isn't on the server yet (python manage.py fetch_terrain)."}
    n = max(2, min(MAX_SAMPLES, int(total // STEP_M) + 1))
    a_tower = g_tower + tower_height
    a_client = g_client + client_height
    f_ghz = freq_mhz / 1000
    worst = None          # (clearance - 0.6*r1, ...) the tightest sample
    blocked_at = None
    points = []
    for i in range(n + 1):
        t = i / n
        d1 = total * t
        d2 = total - d1
        lat = tower_lat + (client_lat - tower_lat) * t
        lng = tower_lng + (client_lng - tower_lng) * t
        ground = elevation(lat, lng)
        if ground is None:
            return {"available": False, "reason": "Part of this path is outside the downloaded terrain."}
        bulge = d1 * d2 / (2 * K_FACTOR * EARTH_R)
        sight = a_tower + (a_client - a_tower) * t
        r1 = 17.32 * math.sqrt((d1 / 1000) * (d2 / 1000) / (f_ghz * max(total / 1000, 1e-6))) if 0 < t < 1 else 0.0
        clearance = sight - (ground + bulge)
        points.append({
            "d": round(d1), "ground": round(ground + bulge, 1), "sight": round(sight, 1),
            "fresnel_low": round(sight - 0.6 * r1, 1),
        })
        if d1 < END_SKIP_M or d2 < END_SKIP_M:
            continue
        margin = clearance - 0.6 * r1
        if worst is None or margin < worst[0]:
            worst = (margin, d1, clearance, r1)
        if clearance < 0 and (blocked_at is None or clearance < blocked_at[1]):
            blocked_at = (d1, clearance)
    if blocked_at is not None:
        verdict = "blocked"
        detail = f"Terrain is {abs(blocked_at[1]):.0f} m above the sight line {blocked_at[0] / 1000:.2f} km from the tower."
    elif worst is not None and worst[0] < 0:
        verdict = "marginal"
        pct = max(0, min(100, round(100 * worst[2] / worst[3]))) if worst[3] else 100
        detail = f"Sight line clears, but only {pct}% of the Fresnel zone is clear {worst[1] / 1000:.2f} km from the tower (60% wanted)."
    else:
        verdict = "clear"
        detail = "Clear of terrain with 60% of the Fresnel zone free."
    # Thin the profile for the chart.
    step = max(1, len(points) // 150)
    profile = points[::step]
    if profile[-1] is not points[-1]:
        profile.append(points[-1])
    return {
        "available": True,
        "verdict": verdict,
        "detail": detail,
        "distance_m": round(total),
        "tower_ground_m": round(g_tower, 1),
        "tower_height_m": tower_height,
        "client_ground_m": round(g_client, 1),
        "client_height_m": client_height,
        "freq_mhz": freq_mhz,
        "profile": profile,
    }


# --- fetching tiles (management command) -----------------------------------------

def convert_tile(tif_path, lat_floor, lng_floor):
    """GeoTIFF -> int16 .npy + bounds .json, georeferenced from the file's tags."""
    import json

    import numpy as np
    import tifffile

    with tifffile.TiffFile(tif_path) as tif:
        page = tif.pages[0]
        arr = page.asarray()
        scale = page.tags["ModelPixelScaleTag"].value
        tie = page.tags["ModelTiepointTag"].value
    dx, dy = float(scale[0]), float(scale[1])
    # tiepoint: (i, j, k, x, y, z) -- raster (i, j) at map (x, y)
    west = float(tie[3]) - float(tie[0]) * dx
    north = float(tie[4]) + float(tie[1]) * dy
    data = np.nan_to_num(np.asarray(arr, dtype="float32"), nan=0.0)
    data = np.clip(np.rint(data), -500, 9000).astype("int16")
    os.makedirs(terrain_dir(), exist_ok=True)
    npy, meta = _paths(lat_floor, lng_floor)
    np.save(npy, data)
    with open(meta, "w") as fh:
        json.dump({"west": west, "north": north, "dx": dx, "dy": dy,
                   "rows": int(data.shape[0]), "cols": int(data.shape[1]), "source": "Copernicus DEM GLO-30"}, fh)
    _tile.cache_clear()
    return data.shape
