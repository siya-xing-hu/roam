import json
from copy import deepcopy
from io import BytesIO
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fetch_provincial_tourism import parse_records, to_item, xlsx_tables
from publish_catalog import publish
from geocode_amap import classify_response, POI_ENDPOINT
from reuse_catalog_pois import link


class CatalogTests(unittest.TestCase):
    def test_daily_quota_stops_collection_instead_of_retrying(self):
        for code in ("10003", "10044", "10045"):
            record = {"response": {"httpStatus": 200, "complete": True, "body": {"status": "0", "infocode": code}}}
            self.assertEqual(classify_response(record)[0], "fatal_error")

    def test_cached_pois_require_matching_region_and_preserve_raw_response(self):
        record = {"requestId": "real-request", "queryFingerprint": "fingerprint", "attractionIds": ["original"],
                  "request": {"endpoint": POI_ENDPOINT}, "response": {"httpStatus": 200, "complete": True,
                  "body": {"status": "1", "count": "1", "pois": [{"id": "poi-1", "name": "甲公园",
                  "type": "风景名胜;公园广场;公园", "pname": "浙江省", "cityname": "杭州市",
                  "adname": "西湖区", "adcode": "330106", "location": "120.1,30.2", "address": "甲路1号"}]}}}
        saved = deepcopy(record)
        base = {"name": "甲公园", "reviewStatus": "ready", "provinceHints": ["浙江省"],
                "classifications": [{"type": "a-level", "value": "4A"}]}
        items = [{**base, "id": "correct", "regionText": "浙江省杭州市西湖区"},
                 {**base, "id": "wrong-city", "regionText": "浙江省宁波市"},
                 {**base, "id": "manual-review", "regionText": "浙江省杭州市西湖区", "reviewStatus": "needs_review"}]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("reuse_catalog_pois.read_archive", return_value={"requests": [record]}), \
                 patch("reuse_catalog_pois.read_source", return_value={"items": items}):
                output = link(root / "source.json", [root / "raw.json"], root / "linked.json")
            self.assertEqual(output["requests"][0]["attractionIds"], ["original", "correct"])
            self.assertEqual(output["requests"][0]["response"], saved["response"])
            self.assertEqual(record, saved)

    def test_xlsx_merged_regions_do_not_fill_unmerged_blanks(self):
        output = BytesIO()
        ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
        with ZipFile(output, "w") as archive:
            archive.writestr("xl/workbook.xml", f'<workbook {ns} xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="景区" sheetId="1" r:id="r1"/></sheets></workbook>')
            archive.writestr("xl/_rels/workbook.xml.rels", '<Relationships><Relationship Id="r1" Target="worksheets/sheet1.xml"/></Relationships>')
            archive.writestr("xl/worksheets/sheet1.xml", f'''<worksheet {ns}><sheetData>
                <row r="1"><c r="A1" t="inlineStr"><is><t>杭州市</t></is></c><c r="B1" t="inlineStr"><is><t>西湖区</t></is></c></row>
                <row r="2"><c r="C2" t="inlineStr"><is><t>景点二</t></is></c></row>
                <row r="3"><c r="C3" t="inlineStr"><is><t>景点三</t></is></c><c r="XFD3"/></row>
                </sheetData><mergeCells><mergeCell ref="A1:A2"/></mergeCells></worksheet>''')
        _, rows = xlsx_tables(output.getvalue())[0]
        self.assertEqual(rows[1], ["杭州市", "", "景点二"])
        self.assertEqual(rows[2], ["", "", "景点三"])

    def test_bad_numbered_grade_does_not_silently_drop_row(self):
        source = {"id": "test", "file": "test.html", "columns": {"name": "景区名称", "grade": "等级"}}
        body = '<table><tr><th>序号</th><th>景区名称</th><th>等级</th></tr><tr><td>1</td><td>甲公园</td><td>待定</td></tr></table>'
        with self.assertRaisesRegex(ValueError, "无法识别等级"):
            parse_records(source, body.encode())

    def test_row_reordering_preserves_identity_and_ambiguous_name_stays_pending(self):
        source = {"id": "test", "province": "浙江省", "name": "官方名录", "url": "https://example.gov.cn", "date": "2026-01-01"}
        record = dict(name="甲公园·乙古镇", city="杭州市", district="西湖区", grade="4A", sheet="名录", row=1)
        a, b = to_item(source, record), to_item(source, {**record, "row": 20})
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(a["reviewStatus"], "needs_review")

    def test_hunan_swapped_grade_is_corrected_only_for_explicit_source(self):
        source = {"id": "test", "file": "test.html", "gradeMayPrecedeRegion": True,
                  "columns": {"name": "景区名称", "region": "所在地市", "grade": "等级"}}
        body = '<table><tr><th>景区名称</th><th>所在地市</th><th>等级</th></tr><tr><td>甲公园</td><td>4A</td><td>耒阳市</td></tr></table>'.encode()
        record = parse_records(source, body)[0]
        self.assertEqual((record["region"], record["grade"]), ("耒阳市", "4A"))
        self.assertIn("columnCorrection", record)
        with self.assertRaisesRegex(ValueError, "无法识别等级"):
            parse_records({**source, "gradeMayPrecedeRegion": False}, body)

    def test_sheet_grades_and_different_headers_are_all_read(self):
        source = {"id": "test", "file": "test.xlsx", "gradeFromSheet": True,
                  "columns": {"name": "景区名称", "city": "设区市", "district": "区县", "grade": "等级"},
                  "sheetColumns": {"2A景区": {"name": "景区名称", "city": "地区", "grade": "等级"}}}
        tables = [("4A景区", [["设区市", "区县", "景区名称"], ["南昌市", "西湖区", "甲公园"]]),
                  ("2A景区", [["地 区", "景区名称"], ["抚州市", "乙公园"]])]
        with patch("fetch_provincial_tourism.xlsx_tables", return_value=tables):
            records = parse_records(source, b"")
        self.assertEqual([r["grade"] for r in records], ["4A", "2A"])
        self.assertEqual(records[1]["city"], "抚州市")

    def test_incremental_publish_preserves_old_and_excludes_unresolved_and_duplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, archive, output, report = [root / name for name in ("source.json", "archive.json", "map.json", "report.json")]
            old = {"id": "old", "name": "原有景点", "level": 5, "province": "浙江省"}
            output.write_text(json.dumps({"schemaVersion": 1, "coordSystem": "GCJ-02", "items": [old]}))
            records = [{"id": key, "name": key, "regionText": "浙江省", "sources": [], "classifications": []}
                       for key in ("new", "unknown", "duplicate")]
            def match(item, *args, **kwargs):
                return {"matchStatus": "pending" if item["id"] == "unknown" else "matched",
                        "matchReason": "需更多坐标证据", "selected": {"poiId": "same-poi"}}
            def mapped(item, selected):
                return {"id": item["id"], "name": item["name"], "level": 4, "province": "浙江省"}
            with patch("publish_catalog.read_source", return_value={"items": records}), \
                 patch("publish_catalog.read_archive", return_value={"requests": []}), \
                 patch("publish_catalog.matching_state", side_effect=match), \
                 patch("publish_catalog.map_item", side_effect=mapped):
                result = publish(source, [archive], output, report)
            actual = {item["id"]: item for item in json.loads(output.read_text())["items"]}
            self.assertEqual(actual["old"], old)
            self.assertEqual(set(actual), {"old", "new"})
            self.assertEqual(result["summary"]["statuses"], {"pending": 1, "duplicate_poi": 1})

    def test_address_only_result_cannot_add_new_scenic_point(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "map.json"
            old = {"id": "old", "name": "原有景点", "level": 5, "province": "浙江省"}
            output.write_text(json.dumps({"schemaVersion": 1, "coordSystem": "GCJ-02", "items": [old]}))
            item = {"id": "new", "name": "新景点", "regionText": "浙江省", "sources": [], "classifications": []}
            with patch("publish_catalog.read_source", return_value={"items": [item]}), \
                 patch("publish_catalog.read_archive", return_value={"requests": []}), \
                 patch("publish_catalog.matching_state", return_value={"matchStatus": "matched", "matchReason": "", "selected": {"poiId": ""}}):
                result = publish(root / "source.json", [root / "raw.json"], output, root / "report.json")
            self.assertEqual(json.loads(output.read_text())["items"], [old])
            self.assertEqual(result["summary"]["added"], 0)
            self.assertEqual(result["summary"]["statuses"], {"needs_review": 1})


if __name__ == "__main__":
    unittest.main()
