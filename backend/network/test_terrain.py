"""Line of sight over terrain (#179)."""
import os
import shutil
import tempfile

import numpy as np
import tifffile
from django.test import SimpleTestCase, TestCase, override_settings

from network import terrain


def write_tile(path, hill=None):
    """A 1x1 degree GeoTIFF S28 E027 (west 27, north -27), 361x361 pixels,
    flat at 1400 m with an optional hill, written like Copernicus' COGs
    (deflate + floating point predictor, tiled)."""
    n = 361
    data = np.full((n, n), 1400.0, dtype="float32")
    if hill:
        (row, col, radius, height) = hill
        yy, xx = np.mgrid[0:n, 0:n]
        data += np.clip(height - np.hypot(yy - row, xx - col) * (height / radius), 0, None).astype("float32")
    d = 1 / n
    tifffile.imwrite(
        path, data, tile=(256, 256), compression="deflate", predictor=3,
        extratags=[(33550, 12, 3, (d, d, 0.0)), (33922, 12, 6, (0.0, 0.0, 0.0, 27.0, -27.0, 0.0))],
    )


class TerrainTests(SimpleTestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.override = override_settings(TERRAIN_DIR=self.dir)
        self.override.enable()
        terrain._tile.cache_clear()

    def tearDown(self):
        self.override.disable()
        terrain._tile.cache_clear()
        shutil.rmtree(self.dir)

    def load(self, hill=None):
        tif = os.path.join(self.dir, "t.tif")
        write_tile(tif, hill)
        shape = terrain.convert_tile(tif, -28, 27)
        self.assertEqual(shape, (361, 361))

    def test_tile_name(self):
        self.assertEqual(terrain.tile_name(-28, 27), "Copernicus_DSM_COG_10_S28_00_E027_00_DEM")

    def test_elevation_and_georeference(self):
        self.load(hill=(180, 180, 40, 200))       # hill top at the tile's centre
        self.assertAlmostEqual(terrain.elevation(-27.95, 27.05), 1400, delta=1)
        self.assertGreater(terrain.elevation(-27.5, 27.5), 1580)
        self.assertIsNone(terrain.elevation(-26.5, 27.5))          # no tile there
        self.assertTrue(terrain.available())

    def test_clear_over_flat_ground(self):
        self.load()
        r = terrain.line_of_sight(-27.60, 27.20, 15, -27.62, 27.25, 9, 5500)
        self.assertTrue(r["available"])
        self.assertEqual(r["verdict"], "clear")
        self.assertAlmostEqual(r["distance_m"], 5400, delta=300)
        self.assertGreater(len(r["profile"]), 50)

    def test_blocked_by_a_hill_in_between(self):
        self.load(hill=(180, 180, 30, 120))
        r = terrain.line_of_sight(-27.5, 27.40, 20, -27.5, 27.60, 9, 5500)   # straight over the hill
        self.assertEqual(r["verdict"], "blocked")
        self.assertIn("above the sight line", r["detail"])

    def test_marginal_when_the_fresnel_zone_is_clipped(self):
        # A low rise: under the sight line, inside 60% of the Fresnel zone.
        self.load(hill=(180, 180, 60, 14))
        r = terrain.line_of_sight(-27.5, 27.40, 20, -27.5, 27.60, 9, 5500)
        self.assertEqual(r["verdict"], "marginal", r["detail"])

    def test_without_terrain(self):
        r = terrain.line_of_sight(-27.6, 27.2, 15, -27.62, 27.25)
        self.assertFalse(r["available"])
        self.assertFalse(terrain.available())



class LosInCheckTests(TestCase):
    """The serviceability check carries line of sight for the nearest towers."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.override = override_settings(TERRAIN_DIR=self.dir, UISP_URL="https://uisp.test", UISP_TOKEN="t")
        self.override.enable()
        terrain._tile.cache_clear()

    def tearDown(self):
        self.override.disable()
        terrain._tile.cache_clear()
        shutil.rmtree(self.dir)

    def test_check_point_includes_los(self):
        from unittest import mock

        from django.core.cache import cache
        from network import geo
        from network.test_uisp import fake_get

        cache.clear()
        with mock.patch("network.uisp.requests.get", side_effect=fake_get):
            here = next(s for s in geo.uisp.sites() if s["name"] == "Winter Hoek")
            lat, lng = here["latitude"] + 0.0003, here["longitude"] + 0.0003
            # A flat tile wherever the fixture towers are.
            tif = os.path.join(self.dir, "t.tif")
            la, lo = int(np.floor(lat)), int(np.floor(lng))
            n = 361
            d = 1 / n
            tifffile.imwrite(tif, np.full((n, n), 1400.0, dtype="float32"), compression="deflate",
                             extratags=[(33550, 12, 3, (d, d, 0.0)), (33922, 12, 6, (0.0, 0.0, 0.0, float(lo), float(la + 1), 0.0))])
            terrain.convert_tile(tif, la, lo)
            r = geo.check_point(lat, lng, client_height=12)
        near = r["nearest"][0]
        self.assertEqual(near["name"], "Winter Hoek")
        self.assertTrue(near["los"]["available"], near["los"])
        self.assertEqual(near["los"]["verdict"], "clear")
        self.assertEqual(near["los"]["client_height_m"], 12)
        self.assertEqual(near["los"]["tower_height_m"], float(here["height"] or 10))
        self.assertTrue(r["terrain_loaded"])
