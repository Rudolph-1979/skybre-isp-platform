"""Coverage import from KML/KMZ and the per-house check (#178)."""
import io
import json
import zipfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from network import coverage
from network.models import CoverageArea, CoverageLayer, CoveragePoint

User = get_user_model()

# A tiny file shaped like Vuma's: a Kroonstad suburb with wall boxes and home
# drops, a regional-office container (one excluded office), and a WC polygon
# hiding in an innocent folder.
KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>Fibre coverage</name>
<Folder><name>Testdorp (KTD).kmz</name>
  <Folder><name>Wall Box</name>
    <Placemark><name>WB001</name><Point><coordinates>27.2500,-27.6600,0</coordinates></Point></Placemark>
  </Folder>
  <Folder><name>Home Drop</name>
    <Placemark><name>WB001 - U001</name><LineString><coordinates>27.2500,-27.6600,0 27.2502,-27.6602,0</coordinates></LineString></Placemark>
    <Placemark><name>WB001 - U002</name><LineString><coordinates>27.2500,-27.6600,0 27.2498,-27.6598,0</coordinates></LineString></Placemark>
  </Folder>
  <Folder><name>Other</name>
    <Placemark><name>Testdorp</name><Polygon><outerBoundaryIs><LinearRing><coordinates>
      27.24,-27.67,0 27.26,-27.67,0 27.26,-27.65,0 27.24,-27.65,0 27.24,-27.67,0</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
  </Folder>
</Folder>
<Folder><name>Wallbox Only (KWO).kmz</name>
  <Folder><name>Wall Box (2)</name>
    <Placemark><name>WB200</name><Point><coordinates>27.2300,-27.6800,0</coordinates></Point></Placemark>
    <Placemark><name>WB201</name><Point><coordinates>27.2310,-27.6810,0</coordinates></Point></Placemark>
  </Folder>
</Folder>
<Folder><name>master_core 2026-04-16 13_04.kml</name>
  <Folder><name>Cape Town Regional Office</name>
    <Placemark><name>AURORA_VC_AE_L</name><Polygon><outerBoundaryIs><LinearRing><coordinates>
      18.6,-33.8,0 18.7,-33.8,0 18.7,-33.9,0 18.6,-33.9,0 18.6,-33.8,0</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
  </Folder>
  <Folder><name>Secondary Cities Regional Office</name>
    <Placemark><name>KROONSTAD_VC_GP_L</name><Polygon><outerBoundaryIs><LinearRing><coordinates>
      27.20,-27.70,0 27.30,-27.70,0 27.30,-27.60,0 27.20,-27.60,0 27.20,-27.70,0</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
    <Placemark><name>PLANNED_VC_GP_B</name><Polygon><outerBoundaryIs><LinearRing><coordinates>
      27.00,-28.00,0 27.05,-28.00,0 27.05,-27.95,0 27.00,-27.95,0 27.00,-28.00,0</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
    <Placemark><name>SNEAKY_WC_VC_GP_L</name><Polygon><outerBoundaryIs><LinearRing><coordinates>
      18.4,-33.9,0 18.5,-33.9,0 18.5,-34.0,0 18.4,-34.0,0 18.4,-33.9,0</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
  </Folder>
</Folder>
</Document></kml>"""


class CoverageParseTests(TestCase):
    def setUp(self):
        self.doc = coverage.read_kml(KML.encode())

    def test_preview_folders(self):
        rows = {r["path"]: r for r in coverage.preview(self.doc)}
        town = rows["Testdorp (KTD).kmz"]
        self.assertEqual((town["areas"], town["houses"], town["wall_boxes"]), (1, 2, 0))
        self.assertTrue(town["default"])
        self.assertEqual(rows["Wallbox Only (KWO).kmz"]["wall_boxes"], 2)
        ct = rows["master_core 2026-04-16 13_04.kml / Cape Town Regional Office"]
        self.assertTrue(ct["excluded"])
        self.assertFalse(ct["default"])
        self.assertTrue(rows["master_core 2026-04-16 13_04.kml / Secondary Cities Regional Office"]["default"])

    def test_import_houses_areas_and_exclusions(self):
        paths = [r["path"] for r in coverage.preview(self.doc)]   # tick everything, even Cape Town
        layer, dropped = coverage.import_layer(self.doc, paths, name="Vuma", provider="Vuma", kind="fibre")
        self.assertEqual(dropped, 1)                               # the WC polygon in Secondary Cities
        codes = set(CoverageArea.objects.values_list("code", flat=True))
        self.assertEqual(codes, {"Testdorp", "KROONSTAD_VC_GP_L", "PLANNED_VC_GP_B"})
        self.assertFalse(CoverageArea.objects.get(code="PLANNED_VC_GP_B").live)
        houses = CoveragePoint.objects.filter(kind="house")
        self.assertEqual(houses.count(), 2)                        # home drop far ends, not the wall box
        self.assertAlmostEqual(houses.get(name="WB001 - U001").lat, -27.6602)
        self.assertEqual(CoveragePoint.objects.filter(kind="wall_box").count(), 2)   # suburb without drops
        self.assertEqual((layer.area_count, layer.point_count), (3, 4))

    def test_check_house_area_and_outside(self):
        coverage.import_layer(self.doc, [r["path"] for r in coverage.preview(self.doc)], name="V", provider="Vuma", kind="fibre")
        at_house = coverage.check(-27.66021, 27.25021)
        self.assertTrue(at_house["covered"])
        self.assertEqual(at_house["houses"][0]["name"], "WB001 - U001")
        self.assertLessEqual(at_house["houses"][0]["distance_m"], 5)
        in_area = coverage.check(-27.62, 27.28)          # inside KROONSTAD_VC_GP_L only
        self.assertTrue(in_area["covered"])
        self.assertEqual(in_area["houses"], [])
        self.assertIn("KROONSTAD_VC_GP_L", in_area["note"])
        planned = coverage.check(-27.97, 27.02)          # only the _B area
        self.assertFalse(planned["covered"])
        outside = coverage.check(-26.0, 28.0)
        self.assertFalse(outside["covered"])
        CoverageLayer.objects.update(active=False)
        self.assertFalse(coverage.check(-27.62, 27.28)["loaded"])

    def test_kmz_and_hostile_files(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("doc.kml", KML)
        doc = coverage.read_kml(buf.getvalue())
        self.assertEqual(len(coverage.preview(doc)), 4)
        with self.assertRaises(coverage.CoverageError):
            coverage.read_kml(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><kml/>')
        with self.assertRaises(coverage.CoverageError):
            coverage.read_kml(b"not xml at all")

    def test_point_in_polygon_with_hole(self):
        outer = [[0, 0], [10, 0], [10, 10], [0, 10]]
        hole = [[4, 4], [6, 4], [6, 6], [4, 6]]
        self.assertTrue(coverage.inside(2, 2, [outer, hole]))
        self.assertFalse(coverage.inside(5, 5, [outer, hole]))
        self.assertFalse(coverage.inside(12, 5, [outer]))


class CoverageApiTests(TestCase):
    def setUp(self):
        self.boss = APIClient()
        self.boss.force_authenticate(User.objects.create_user(username="boss", password="x", role="admin"))

    def upload(self, client, url, **extra):
        return client.post(url, {"file": SimpleUploadedFile("Fibre coverage.kml", KML.encode()), **extra}, format="multipart")

    def test_preview_import_houses_and_check(self):
        pv = self.upload(self.boss, "/api/map/coverage/preview/")
        self.assertEqual(pv.status_code, 200)
        ticked = [f["path"] for f in pv.data["folders"] if f["default"]]
        res = self.upload(self.boss, "/api/map/coverage/import/", folders=json.dumps(ticked), name="Vuma fibre", provider="Vuma", kind="fibre")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["houses"], 4)
        dots = self.boss.get("/api/map/coverage/houses/?bbox=27.2,-27.7,27.3,-27.6").data["houses"]
        self.assertEqual(len(dots), 4)
        layers = self.boss.get("/api/map/coverage/layers/").data
        self.assertTrue(layers["can_manage"])
        lid = layers["results"][0]["id"]
        # a re-import replaces the layer
        res2 = self.upload(self.boss, "/api/map/coverage/import/", folders=json.dumps(ticked), name="Vuma fibre", provider="Vuma", replace=str(lid))
        self.assertEqual(res2.status_code, 201)
        self.assertEqual(CoverageLayer.objects.count(), 1)
        self.assertEqual(self.boss.patch(f"/api/map/coverage/layers/{res2.data['id']}/", {"active": False}, format="json").status_code, 200)
        self.assertEqual(self.boss.delete(f"/api/map/coverage/layers/{res2.data['id']}/").status_code, 204)
        self.assertEqual(CoveragePoint.objects.count(), 0)

    def test_only_admin_or_management_import(self):
        staff = APIClient()
        staff.force_authenticate(User.objects.create_user(username="net", password="x", role="support", allowed_sections=["networking"]))
        self.assertEqual(self.upload(staff, "/api/map/coverage/preview/").status_code, 403)
        self.assertEqual(staff.get("/api/map/coverage/layers/").status_code, 200)

    def test_bad_files(self):
        res = self.boss.post("/api/map/coverage/preview/", {"file": SimpleUploadedFile("x.pdf", b"%PDF")}, format="multipart")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.boss.post("/api/map/coverage/preview/", {}, format="multipart").status_code, 400)
