"""Coverage from KML / KMZ files (#178, Rudolph 15 Oct 2026).

"Servicable locations will be brought in by KMZ files which we import" and
"I only want to see a dot for every house that can be possibly serviced on
the map". Built against Vuma's "Fibre coverage" file:

* Coverage AREAS: every polygon (Vuma's master_core areas such as
  KROONSTAD_VC_GP_L, MDU areas, the Kroonstad suburb outlines). Not drawn on
  the map; the house check says when a point is inside one. A name ending
  "_L" (or status LIVE) is live; "_B" is shown as not live because what B
  means is not known.
* HOUSES: the far end of every "Home Drop" cable (its near end is the wall
  box, ~24 m away -- measured on Jordania, Panorama and Wilgenhof). A suburb
  whose file has no home drops (Suidrand) gets its wall boxes instead, each
  standing for the house(s) next to it. These are the dots on the map.
* Never imported (Rudolph: "Western Cape, Durban and Eastern Cape should be
  excluded from the import"): folders named for those regions (Cape Town /
  Durban Regional Office, MDU ... WC / KZN / EC) can't be ticked, and any
  area or house whose position falls in those provinces is dropped even when
  it sits in another folder (old CoreTac sets mix regions). The province test
  is a rough box, see _excluded_region().

The upload is read and thrown away; only the selected areas and houses are
stored. No PostGIS: areas keep a bounding box for the database to filter on
and the exact point-in-polygon test runs in Python.
"""
import io
import math
import re
import zipfile
import xml.etree.ElementTree as ET

from django.db import transaction

K = "{http://www.opengis.net/kml/2.2}"
MAX_UPLOAD_BYTES = 40 * 1024 * 1024
MAX_KML_BYTES = 150 * 1024 * 1024   # inside a KMZ (zip bomb guard)
HOUSE_NEAR_M = 30                   # a house dot this close counts as "this house"

EXCLUDED_FOLDER = re.compile(
    r"cape town|western cape|durban|kwazulu|kzn|eastern cape|\bwc\b|\bec\b", re.I
)


class CoverageError(Exception):
    pass


# --- reading the file ---------------------------------------------------------

def read_kml(data: bytes):
    """The KML document element from KML or KMZ bytes."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise CoverageError(f"File is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    if data[:2] == b"PK":
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise CoverageError("That KMZ isn't a readable zip file.") from exc
        names = [i for i in zf.infolist() if i.filename.lower().endswith(".kml")]
        if not names:
            raise CoverageError("No .kml inside that KMZ.")
        info = next((i for i in names if i.filename.lower().endswith("doc.kml")), names[0])
        if info.file_size > MAX_KML_BYTES:
            raise CoverageError("The KML inside that KMZ is too large.")
        data = zf.read(info)
    # External entities / entity expansion: KML never needs a DOCTYPE.
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise CoverageError("That file declares a DOCTYPE/ENTITY, which a KML file never needs.")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise CoverageError(f"Not a readable KML file ({exc}).") from exc
    doc = root.find(K + "Document")
    if doc is None:
        doc = root
    return doc


def _name(el):
    return (el.findtext(K + "name") or "").strip()


def _schema_fields(doc):
    out = {}
    for sf in doc.iter(K + "SimpleField"):
        dn = sf.find(K + "displayName")
        out[sf.get("name")] = (dn.text if dn is not None else sf.get("name")) or sf.get("name")
    return out


def _data(pm, fields):
    d = {}
    for sd in pm.iter(K + "SimpleData"):
        d[fields.get(sd.get("name"), sd.get("name"))] = (sd.text or "").strip()
    for dt in pm.iter(K + "Data"):
        d[dt.get("name")] = (dt.findtext(K + "value") or "").strip()
    return d


def _coords(text):
    out = []
    for t in (text or "").split():
        p = t.split(",")
        if len(p) >= 2:
            try:
                out.append([float(p[0]), float(p[1])])
            except ValueError:
                continue
    return out


def _polygons(pm):
    """[[outer, hole, ...], ...] in [lng, lat] for every Polygon in a Placemark."""
    out = []
    for poly in pm.iter(K + "Polygon"):
        outer = poly.find(f"{K}outerBoundaryIs/{K}LinearRing/{K}coordinates")
        if outer is None:
            continue
        ring = _coords(outer.text)
        if len(ring) < 3:
            continue
        rings = [ring]
        for inner in poly.findall(f"{K}innerBoundaryIs/{K}LinearRing/{K}coordinates"):
            hole = _coords(inner.text)
            if len(hole) >= 3:
                rings.append(hole)
        out.append(rings)
    return out


def _excluded_region(lat, lng):
    """Rough boxes for the provinces Rudolph excluded. Western Cape: west of
    24.5 E and south of 30 S. Eastern Cape: south of 31.3 S between 24.5 and
    30.2 E. KwaZulu-Natal: east of 29.6 E between 27.5 and 31.2 S. Close to
    the borders they are approximate."""
    if lng < 24.5 and lat < -30:
        return "Western Cape"
    if 24.5 <= lng <= 30.2 and lat < -31.3:
        return "Eastern Cape"
    if lng > 29.6 and -31.2 < lat < -27.5:
        return "KwaZulu-Natal"
    return None


# --- the folders on offer ------------------------------------------------------

def _children(el):
    return [c for c in el if c.tag == K + "Folder"]


def _units(doc):
    """The pickable groups: top folders, or their subfolders when a top
    folder is just a container of polygon sets (Vuma's regional offices)."""
    units = []
    loose = [c for c in doc if c.tag == K + "Placemark"]
    if loose:
        units.append(("(top level)", doc, True))
    for top in _children(doc):
        subs = _children(top)
        polygon_sets = [
            s for s in subs
            if any(True for _ in s.iter(K + "Polygon")) and not any(True for _ in s.iter(K + "Point"))
        ]
        if len(polygon_sets) >= 2 and len(polygon_sets) == len([s for s in subs if any(True for _ in s.iter(K + "Placemark"))]):
            for s in subs:
                units.append((f"{_name(top)} / {_name(s)}", s, False))
        else:
            units.append((_name(top), top, False))
    return units


def _houses(el):
    """[(name, kind, lat, lng)] for one suburb-ish folder."""
    drops = []
    wall_boxes = []
    for folder in el.iter(K + "Folder"):
        fname = _name(folder).lower()
        for pm in folder.findall(K + "Placemark"):
            if "home drop" in fname:
                line = pm.find(f".//{K}LineString/{K}coordinates")
                pts = _coords(line.text) if line is not None else []
                if len(pts) >= 2:
                    drops.append((_name(pm), pts[-1]))
            elif "wall box" in fname:
                pt = pm.find(f".//{K}Point/{K}coordinates")
                pts = _coords(pt.text) if pt is not None else []
                if pts:
                    wall_boxes.append((_name(pm), pts[0]))
    out, seen = [], set()
    if drops:
        for name, (lng, lat) in drops:
            key = (round(lat, 5), round(lng, 5))   # ~1 m: two drops to one house
            if key in seen:
                continue
            seen.add(key)
            out.append((name, "house", lat, lng))
    else:
        for name, (lng, lat) in wall_boxes:
            out.append((name, "wall_box", lat, lng))
    return out


def _latest_master_core(units):
    dates = sorted({m.group(1) for path, _, _ in units for m in [re.search(r"master_core (\d{4}-\d{2}-\d{2})", path)] if m})
    return dates[-1] if dates else None


def preview(doc):
    fields = _schema_fields(doc)
    del fields  # (kept for symmetry with import; preview only counts)
    units = _units(doc)
    newest = _latest_master_core(units)
    out = []
    for path, el, _ in units:
        areas = sum(1 for pm in el.iter(K + "Placemark") if pm.find(f".//{K}Polygon") is not None)
        houses = _houses(el) if el is not doc else []
        excluded = bool(EXCLUDED_FOLDER.search(path))
        older = False
        m = re.search(r"master_core (\d{4}-\d{2}-\d{2})", path)
        if m and newest and m.group(1) != newest:
            older = True
        if re.match(r"coretac", path, re.I) or "CoreTac" in path:
            older = True
        out.append({
            "path": path,
            "areas": areas,
            "houses": sum(1 for h in houses if h[1] == "house"),
            "wall_boxes": sum(1 for h in houses if h[1] == "wall_box"),
            "excluded": excluded,
            "default": not excluded and not older and (areas or houses),
            "note": "Western Cape / Durban / Eastern Cape -- never imported" if excluded
            else ("Older copy -- a newer set is in the file" if older else ""),
        })
    return out


# --- importing -------------------------------------------------------------------

def import_layer(doc, paths, *, name, provider, kind, user=None, source_file="", replace=None):
    from .models import CoverageArea, CoverageLayer, CoveragePoint

    fields = _schema_fields(doc)
    wanted = [(p, el) for p, el, _ in _units(doc) if p in set(paths) and not EXCLUDED_FOLDER.search(p)]
    if not wanted:
        raise CoverageError("Tick at least one folder to import.")
    areas, points, dropped = [], [], 0
    for path, el in wanted:
        placemarks = el.findall(K + "Placemark") if el is doc else el.iter(K + "Placemark")
        for pm in placemarks:
            polys = _polygons(pm)
            if not polys:
                continue
            d = _data(pm, fields)
            nm = d.get("name") or _name(pm)
            code = d.get("code") or _name(pm)
            status = (d.get("status") or "").upper()
            suffix = code.rsplit("_", 1)[-1] if "_" in code else ""
            if not status and suffix == "L":
                status = "LIVE"
            elif not status and suffix == "B":
                status = "B"
            live = status in ("", "LIVE")
            network = d.get("network") or ""
            for rings in polys:
                lngs = [p[0] for p in rings[0]]
                lats = [p[1] for p in rings[0]]
                cy, cx = sum(lats) / len(lats), sum(lngs) / len(lngs)
                if _excluded_region(cy, cx):
                    dropped += 1
                    continue
                areas.append(CoverageArea(
                    name=nm[:255], code=code[:120], status=status[:40], live=live, network=network[:40],
                    folder=path[:500], rings=rings,
                    min_lat=min(lats), max_lat=max(lats), min_lng=min(lngs), max_lng=max(lngs),
                ))
        if el is not doc:
            for hname, hkind, lat, lng in _houses(el):
                if _excluded_region(lat, lng):
                    dropped += 1
                    continue
                area = re.sub(r"\.km[lz]$", "", path, flags=re.I).strip()
                points.append(CoveragePoint(name=hname[:255], kind=hkind, area=area[:255], lat=lat, lng=lng))
    with transaction.atomic():
        if replace is not None:
            CoverageLayer.objects.filter(pk=replace).delete()
        layer = CoverageLayer.objects.create(
            name=name[:120] or "Coverage", provider=provider[:60], kind=kind, source_file=source_file[:255],
            folders=[p for p, _ in wanted], area_count=len(areas), point_count=len(points),
            imported_by=user if getattr(user, "pk", None) else None,
        )
        for a in areas:
            a.layer = layer
        for p in points:
            p.layer = layer
        CoverageArea.objects.bulk_create(areas, batch_size=500)
        CoveragePoint.objects.bulk_create(points, batch_size=1000)
    return layer, dropped


# --- the house check ---------------------------------------------------------------

def _inside_ring(lat, lng, ring):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat) and lng < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-15) + xi:
            inside = not inside
        j = i
    return inside


def inside(lat, lng, rings):
    return _inside_ring(lat, lng, rings[0]) and not any(_inside_ring(lat, lng, h) for h in rings[1:])


def _metres(lat1, lng1, lat2, lng2):
    dy = (lat2 - lat1) * 110540
    dx = (lng2 - lng1) * 111320 * math.cos(math.radians(lat1))
    return math.hypot(dx, dy)


def check(lat, lng):
    """What the imported coverage says about one point."""
    from .models import CoverageArea, CoverageLayer, CoveragePoint

    layers = list(CoverageLayer.objects.filter(active=True))
    if not layers:
        return {
            "loaded": False, "covered": None, "areas": [], "houses": [],
            "note": "No coverage imported yet -- import a KML/KMZ on this page. Use the nearest towers as a guide.",
        }
    areas = []
    for a in CoverageArea.objects.filter(
        layer__active=True, min_lat__lte=lat, max_lat__gte=lat, min_lng__lte=lng, max_lng__gte=lng
    ).select_related("layer"):
        if inside(lat, lng, a.rings):
            areas.append({
                "name": a.name, "code": a.code, "network": a.network, "status": a.status, "live": a.live,
                "layer": a.layer.name, "provider": a.layer.provider, "kind": a.layer.kind,
            })
    d_lat = 120 / 110540
    d_lng = 120 / (111320 * max(math.cos(math.radians(lat)), 0.1))
    houses = []
    for p in CoveragePoint.objects.filter(
        layer__active=True, lat__range=(lat - d_lat, lat + d_lat), lng__range=(lng - d_lng, lng + d_lng)
    ).select_related("layer"):
        houses.append({
            "name": p.name, "kind": p.kind, "area": p.area, "lat": p.lat, "lng": p.lng,
            "distance_m": round(_metres(lat, lng, p.lat, p.lng)), "provider": p.layer.provider,
        })
    houses.sort(key=lambda h: h["distance_m"])
    houses = houses[:3]
    near_house = bool(houses and houses[0]["distance_m"] <= HOUSE_NEAR_M)
    live_area = any(a["live"] for a in areas)
    covered = near_house or live_area
    if near_house:
        h = houses[0]
        note = f"Serviceable house marked {h['distance_m']} m from this point ({h['area']}, {h['name']})."
    elif live_area:
        a = next(a for a in areas if a["live"])
        note = f"Inside {a['provider'] or 'the'} live area {a['code'] or a['name']}" + (f" ({a['network']})" if a["network"] else "") + \
            " -- no house marked right here, so confirm with the provider."
    elif areas:
        note = "Only inside an area that isn't marked live."
    else:
        note = "Outside the imported coverage."
    return {"loaded": True, "covered": covered, "areas": areas, "houses": houses, "note": note}
