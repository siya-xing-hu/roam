import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "generate.py"
SPEC = importlib.util.spec_from_file_location("attraction_generator", MODULE_PATH)
assert SPEC and SPEC.loader
GENERATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GENERATOR)


class GeneratorTests(unittest.TestCase):
    def test_normalize_grade_accepts_both_official_styles(self):
        self.assertEqual(GENERATOR.normalize_grade("AAAAA"), "5A")
        self.assertEqual(GENERATOR.normalize_grade("4A级"), "4A")

    def test_parse_jiangsu_rows(self):
        source = {
            "province": "江苏省",
            "sourceName": "江苏省文化和旅游厅",
            "sourceUrl": "https://example.test/list",
            "publishedAt": "2026-03-13",
        }
        html = """
        <ul><li>
          <span>&nbsp;</span><span>测试景区</span><span>南京市</span>
          <span>玄武区</span><span>AAAA</span>
        </li></ul>
        """

        [item] = GENERATOR.parse_jiangsu_rows(html, source)

        self.assertEqual(item["name"], "测试景区")
        self.assertEqual(item["grade"], "4A")
        self.assertEqual(item["address"], "江苏省南京市玄武区测试景区")

    def test_stable_id_is_deterministic(self):
        item = {"province": "上海市", "name": "测试景区"}
        self.assertEqual(GENERATOR.stable_id(item), GENERATOR.stable_id(item))
        self.assertRegex(GENERATOR.stable_id(item), r"^sh-[0-9a-f]{14}$")

    def test_manual_level_preserves_official_tag(self):
        record = {"id": "sh-test", "level": 5, "levelSource": "official", "tags": ["5A"]}
        updated = GENERATOR.apply_override(record, {"level": 8, "tags": ["亲子"]})
        self.assertEqual(updated["level"], 8)
        self.assertEqual(updated["levelSource"], "manual")
        self.assertEqual(updated["tags"], ["5A", "亲子"])

    def test_geocode_error_does_not_leak_key(self):
        item = {
            "province": "上海市",
            "city": "上海市",
            "district": "浦东新区",
            "address": "测试地址",
            "name": "测试景区",
        }
        with (
            patch.object(GENERATOR, "AMAP_KEY", "secret-key"),
            patch.object(
                GENERATOR,
                "fetch_text",
                side_effect=RuntimeError("https://example.test?key=secret-key"),
            ),
            self.assertRaisesRegex(RuntimeError, "^高德地理编码请求失败$") as context,
        ):
            GENERATOR.geocode(item, {})

        self.assertNotIn("secret-key", str(context.exception))


if __name__ == "__main__":
    unittest.main()
