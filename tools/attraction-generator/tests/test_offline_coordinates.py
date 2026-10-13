import json
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from coordinate_math import wgs84_to_gcj02
from geocode_amap import DataError, source_fingerprint
from offline_coordinates import AdminLocator, compatible_admin, file_hash, identity_matches, select_unique
from publish_coordinates import publish, validate_candidate
from shapely import to_wkb
from shapely.geometry import Polygon


class OfflineCoordinateTests(unittest.TestCase):
    def setUp(self):
        self.admin = {"province": "北京市", "city": "北京市", "district": "东城区", "adcode": "110101"}
        self.item = {"id": "test-palace", "name": "测试故宫博物院", "regionText": "北京市东城区",
                     "provinceHints": ["北京市"], "reviewStatus": "ready",
                     "address": "官方地址",
                     "sources": [{"name": "测试官方名录", "url": "https://example.gov.cn"}],
                     "classifications": [{"type": "a-level", "value": "4A"}]}
        self.candidate = {"externalId": "node/1", "url": "https://www.openstreetmap.org/node/1",
                          "original": {"lng": 116.397128, "lat": 39.916527, "coordSystem": "WGS84"},
                          "location": "116.403372,39.917931", **self.admin, "address": "",
                          "tags": {"name": "测试故宫博物院", "tourism": "museum"},
                          "method": "mapped-point", "geometryWkb": None}

    def test_wgs84_conversion_and_actual_boundary_agree(self):
        lng, lat = wgs84_to_gcj02(116.397128, 39.916527)
        self.assertAlmostEqual(lng, 116.403372494, places=8)
        self.assertAlmostEqual(lat, 39.917930749, places=8)
        self.assertEqual(AdminLocator().locate(lng, lat), self.admin)
        self.assertEqual(wgs84_to_gcj02(0, 0), [0, 0])

    def test_same_name_in_wrong_city_or_district_is_rejected(self):
        self.assertTrue(identity_matches(self.item, self.candidate))
        self.assertFalse(identity_matches(self.item, {**self.candidate, "district": "西城区"}))
        self.assertFalse(compatible_admin({"province": "浙江省", "city": "杭州市", "district": ""},
                                          {"province": "浙江省", "city": "宁波市", "district": "海曙区"}))

    def test_town_center_and_facility_cannot_become_scenic_coordinate(self):
        for tags in ({"place": "town"}, {"natural": "peak"}, {"tourism": "attraction", "entrance": "yes"},
                     {"tourism": "hotel"}, {"amenity": "parking", "tourism": "attraction"}):
            self.assertFalse(identity_matches(self.item, {**self.candidate, "tags": {"name": self.item["name"], **tags}}))

    def test_duplicate_node_inside_area_is_resolved_but_separate_places_are_not(self):
        geometry = Polygon([(0, 0), (4, 0), (4, 1), (1, 1), (1, 4), (0, 4)])
        self.assertFalse(geometry.covers(geometry.centroid))
        point = geometry.representative_point()
        area = {**self.candidate, "geometryWkb": to_wkb(geometry, hex=True), "externalId": "way/2"}
        inside = {**self.candidate, "original": {"lng": point.x, "lat": point.y}}
        outside = {**inside, "original": {"lng": 3, "lat": 3}}
        self.assertEqual(select_unique([area, inside]), area)
        self.assertIsNone(select_unique([area, outside]))
        self.assertIsNone(select_unique([inside, outside]))

    def test_changed_source_and_forged_admin_are_rejected(self):
        binding = {"sourceFingerprint": source_fingerprint(self.item), "selected": self.candidate}
        locator = AdminLocator()
        with self.assertRaisesRegex(DataError, "Source changed"):
            validate_candidate({**self.item, "name": "另一个景点"}, binding, locator)
        wrong = deepcopy(binding)
        wrong["selected"]["original"]["lng"] = 120.1
        with self.assertRaisesRegex(DataError, "outside its recorded"):
            validate_candidate(self.item, wrong, locator)

    def test_publish_preserves_existing_values_and_replay_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output, archive, candidates, report = [root / (name + ".json") for name in ("map", "bindings", "candidates", "report")]
            old = {**self.admin, "id": "old", "name": "已有公园", "lng": 116.5, "lat": 39.9, "level": 3}
            output.write_text(json.dumps({"schemaVersion": 1, "coordSystem": "GCJ-02", "items": [old]}))
            candidates.write_text(json.dumps({"items": [self.candidate]}))
            binding = {"id": self.item["id"], "status": "matched", "sourceFingerprint": source_fingerprint(self.item), "selected": self.candidate}
            duplicate = {**self.item, "id": "duplicate"}
            duplicate_binding = {**binding, "id": "duplicate", "sourceFingerprint": source_fingerprint(duplicate)}
            data = {"stage": "coordinate-bindings", "provider": "OpenStreetMap", "candidateArchive": str(candidates),
                    "candidateSha256": file_hash(candidates), "source": {"sha256": "test-snapshot"}, "items": [binding, duplicate_binding]}
            archive.write_text(json.dumps(data))
            with patch("publish_coordinates.read_source", return_value={"items": [self.item, duplicate]}):
                first = publish(root / "source.json", archive, output, report)
                saved = output.read_bytes()
                second = publish(root / "source.json", archive, output, report)
            result = {item["id"]: item for item in json.loads(saved)["items"]}
            self.assertEqual(first["summary"]["added"], 1)
            self.assertEqual(first["summary"]["statuses"], {"possible_duplicate": 1})
            self.assertEqual(second["summary"]["added"], 0)
            self.assertEqual(output.read_bytes(), saved)
            self.assertEqual(result["old"], old)
            self.assertEqual(result[self.item["id"]]["coordinateSource"]["original"], self.candidate["original"])
            self.assertEqual(result[self.item["id"]]["source"]["name"], "测试官方名录")
            self.assertEqual(result[self.item["id"]]["address"], "官方地址")


if __name__ == "__main__":
    unittest.main()
