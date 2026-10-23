"""Download the terrain for line of sight (#179).

    python manage.py fetch_terrain                 # tiles around every UISP site
    python manage.py fetch_terrain --bbox 26.9,-29,27.8,-27   # west,south,east,north
    python manage.py fetch_terrain --force         # download again

Copernicus DEM GLO-30 tiles (1x1 degree, ~30 m) from the public AWS bucket,
converted to int16 .npy under media/terrain (about 26 MB a tile). Tiles
already there are skipped. Safe to run again any time.
"""
import math
import os
import tempfile

import requests
from django.core.management.base import BaseCommand, CommandError

from network import terrain

MARGIN = 0.15


class Command(BaseCommand):
    help = "Download Copernicus 30 m terrain tiles for the wireless line-of-sight check."

    def add_arguments(self, parser):
        parser.add_argument("--bbox", help="west,south,east,north in degrees (default: around the UISP sites)")
        parser.add_argument("--force", action="store_true", help="Download tiles again even if present.")

    def handle(self, *args, **opts):
        if opts["bbox"]:
            try:
                w, s, e, n = [float(x) for x in opts["bbox"].split(",")]
            except ValueError:
                raise CommandError("--bbox must be west,south,east,north")
        else:
            from network import uisp

            if not uisp.configured():
                raise CommandError("UISP isn't connected; give --bbox west,south,east,north instead.")
            pts = [(x["latitude"], x["longitude"]) for x in uisp.sites() if x["latitude"] is not None]
            if not pts:
                raise CommandError("No UISP site has a location; give --bbox.")
            s, n = min(p[0] for p in pts) - MARGIN, max(p[0] for p in pts) + MARGIN
            w, e = min(p[1] for p in pts) - MARGIN, max(p[1] for p in pts) + MARGIN
        cells = [(la, lo) for la in range(math.floor(s), math.floor(n) + 1) for lo in range(math.floor(w), math.floor(e) + 1)]
        if len(cells) > 30:
            raise CommandError(f"{len(cells)} tiles is more than this is meant for (30). Narrow --bbox.")
        self.stdout.write(f"Area {w:.2f},{s:.2f} to {e:.2f},{n:.2f}: {len(cells)} tile(s) -> {terrain.terrain_dir()}")
        os.makedirs(terrain.terrain_dir(), exist_ok=True)
        got = skipped = missing = 0
        for la, lo in cells:
            name = terrain.tile_name(la, lo)
            npy, _ = terrain._paths(la, lo)
            if os.path.exists(npy) and not opts["force"]:
                skipped += 1
                self.stdout.write(f"  {name}: already here")
                continue
            url = terrain.TILE_URL.format(name=name)
            try:
                with requests.get(url, stream=True, timeout=120) as res:
                    if res.status_code == 404:
                        missing += 1
                        self.stdout.write(f"  {name}: no tile (Copernicus has none here)")
                        continue
                    res.raise_for_status()
                    fd, tmp = tempfile.mkstemp(suffix=".tif", dir=terrain.terrain_dir())
                    with os.fdopen(fd, "wb") as fh:
                        for chunk in res.iter_content(1 << 20):
                            fh.write(chunk)
            except requests.RequestException as exc:
                raise CommandError(f"{name}: download failed ({exc.__class__.__name__}: {exc}).")
            try:
                shape = terrain.convert_tile(tmp, la, lo)
            finally:
                os.remove(tmp)
            got += 1
            self.stdout.write(self.style.SUCCESS(f"  {name}: {shape[1]}x{shape[0]} saved"))
        self.stdout.write(f"Done: {got} downloaded, {skipped} already here, {missing} with no tile.")
