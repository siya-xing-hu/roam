#!/usr/bin/env python3
"""一次性生成足迹项目的官方 A 级景区静态数据。"""

from __future__ import annotations

import csv
import hashlib
import html as html_module
import json
import math
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


TOOL_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = TOOL_ROOT.parent.parent
OUTPUT_PATH = PROJECT_ROOT / "public/data/attractions.json"
REPORT_PATH = TOOL_ROOT / "generation-report.json"
CACHE_PATH = TOOL_ROOT / "cache/geocodes.json"
SOURCES_PATH = TOOL_ROOT / "sources.json"
OVERRIDES_PATH = TOOL_ROOT / "overrides.json"
AMAP_KEY = os.environ.get("AMAP_WEB_SERVICE_KEY", "")
GRADE_PATTERN = re.compile(r"^[1-5]A$")
REQUEST_HEADERS = {"User-Agent": "roam-personal-data-generator/0.1"}


def decode_html(value: str) -> str:
    value = re.sub(r"<br\s*/?\s*>", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", "", value)
    value = html_module.unescape(value).replace("\xa0", " ")
    return re.sub(r"\s+", " ", value).strip()


def normalize_grade(value: Any) -> str:
    normalized = str(value or "").upper().replace("级", "").strip()
    if re.fullmatch(r"A{1,5}", normalized):
        return f"{len(normalized)}A"
    return normalized


def as_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def parse_html_rows(html: str, source: dict[str, Any]) -> list[dict[str, Any]]:
    published_match = re.search(
        r"(20\d{2})[-年/.](\d{1,2})[-月/.](\d{1,2})",
        html,
    )
    publication_date = ""
    if published_match:
        year, month, day = published_match.groups()
        publication_date = f"{year}-{month.zfill(2)}-{day.zfill(2)}"

    attractions: list[dict[str, Any]] = []
    rows = re.findall(r"<tr[^>]*>([\s\S]*?)</tr>", html, flags=re.IGNORECASE)
    for row in rows:
        cells = [
            decode_html(cell)
            for cell in re.findall(
                r"<t[dh][^>]*>([\s\S]*?)</t[dh]>",
                row,
                flags=re.IGNORECASE,
            )
        ]
        if len(cells) < 3 or not cells[0].isdigit():
            continue

        grade_from_row = next((cell for cell in cells if GRADE_PATTERN.fullmatch(cell)), "")
        grade = normalize_grade(source.get("grade", grade_from_row))
        name = cells[1]
        address = cells[-1]
        if not name or not GRADE_PATTERN.fullmatch(grade) or not address:
            continue

        attractions.append(
            {
                "name": name,
                "grade": grade,
                "address": address,
                "province": source["province"],
                "sourceName": source["sourceName"],
                "sourceUrl": source["url"],
                "publishedAt": publication_date,
            }
        )

    if not attractions:
        raise RuntimeError(f"{source['url']} 未解析到景区表格，网页结构可能已经变化")
    return attractions


def parse_jiangsu_rows(html: str, source: dict[str, Any]) -> list[dict[str, Any]]:
    attractions: list[dict[str, Any]] = []
    rows = re.findall(r"<li\b[^>]*>([\s\S]*?)</li>", html, flags=re.IGNORECASE)
    for row in rows:
        cells = [
            decode_html(cell)
            for cell in re.findall(
                r"<span\b[^>]*>([\s\S]*?)</span>",
                row,
                flags=re.IGNORECASE,
            )
        ]
        if len(cells) < 5:
            continue

        name, city, district = cells[1:4]
        grade = normalize_grade(cells[4])
        if not name or not GRADE_PATTERN.fullmatch(grade):
            continue

        attractions.append(
            {
                "name": name,
                "grade": grade,
                "city": city,
                "district": district,
                # 江苏公开名录没有门牌地址。这里保留官方地区和名称组成的
                # 检索文本，避免把第三方门牌信息冒充为官方字段。
                "address": f"{source['province']}{city}{district}{name}",
                "province": source["province"],
                "sourceName": source["sourceName"],
                "sourceUrl": source["sourceUrl"],
                "publishedAt": source["publishedAt"],
            }
        )
    return attractions


def fetch_text(url: str) -> str:
    request = Request(url, headers=REQUEST_HEADERS)
    try:
        with urlopen(request, timeout=60) as response:
            content = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
    except HTTPError as error:
        raise RuntimeError(f"{url}: HTTP {error.code}") from error

    try:
        return content.decode(charset)
    except (LookupError, UnicodeDecodeError):
        return content.decode("utf-8-sig")


def with_page_number(url: str, page: int) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["currpage"] = str(page)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def load_jiangsu_portal(source: dict[str, Any]) -> list[dict[str, Any]]:
    first_page = fetch_text(with_page_number(source["searchUrl"], 1))
    page_match = re.search(r"共\s*(\d+)\s*页", decode_html(first_page))
    page_count = int(page_match.group(1)) if page_match else 1
    if page_count < 1 or page_count > 500:
        raise RuntimeError(f"江苏名录页数异常：{page_count}")

    pages = [first_page]
    if page_count > 1:
        urls = [with_page_number(source["searchUrl"], page) for page in range(2, page_count + 1)]
        with ThreadPoolExecutor(max_workers=4) as executor:
            pages.extend(executor.map(fetch_text, urls))

    attractions = [item for page in pages for item in parse_jiangsu_rows(page, source)]
    if not attractions:
        raise RuntimeError(f"{source['sourceUrl']} 未解析到景区名录，网页结构可能已经变化")
    return attractions


HEADER_ALIASES = {
    "景区名称": "name",
    "景点名称": "name",
    "名称": "name",
    "unitname": "name",
    "等级": "grade",
    "景区等级": "grade",
    "level": "grade",
    "地址": "address",
    "景区地址": "address",
    "address": "address",
    "城市": "city",
    "地市": "city",
    "city": "city",
    "区县": "district",
    "district": "district",
    "发布日期": "publishedAt",
    "publishedat": "publishedAt",
    "来源链接": "sourceUrl",
    "sourceurl": "sourceUrl",
}


def normalize_header(header: str) -> str:
    key = header.strip().lower()
    return HEADER_ALIASES.get(key, key)


def normalize_input_rows(
    rows: list[dict[str, Any]],
    source: dict[str, Any],
) -> list[dict[str, Any]]:
    normalized_rows: list[dict[str, Any]] = []
    for input_row in rows:
        row = {normalize_header(str(key)): value for key, value in input_row.items()}
        normalized_rows.append(
            {
                "name": as_text(row.get("name", row.get("unitname", ""))),
                "grade": normalize_grade(row.get("grade", row.get("level", ""))),
                "address": as_text(row.get("address", "")),
                "city": as_text(row.get("city", "")),
                "district": as_text(row.get("district", "")),
                "province": source["province"],
                "sourceName": source["sourceName"],
                "sourceUrl": as_text(row.get("sourceUrl", source.get("sourceUrl", ""))),
                "publishedAt": as_text(row.get("publishedAt", source.get("publishedAt", ""))),
            }
        )
    return normalized_rows


def rows_from_json(value: Any, location: str) -> list[dict[str, Any]]:
    rows = value if isinstance(value, list) else value.get("items") if isinstance(value, dict) else None
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise RuntimeError(f"{location} 必须是对象数组或包含 items 对象数组")
    return rows


def load_source(source: dict[str, Any]) -> list[dict[str, Any]]:
    source_kind = source["kind"]
    if source_kind == "html":
        return parse_html_rows(fetch_text(source["url"]), source)
    if source_kind == "jiangsu-portal":
        return load_jiangsu_portal(source)
    if source_kind == "remote-json":
        parsed = json.loads(fetch_text(source["url"]).lstrip("\ufeff"))
        return normalize_input_rows(rows_from_json(parsed, source["url"]), source)

    path = TOOL_ROOT / source["path"]
    if source_kind == "csv":
        with path.open("r", encoding="utf-8-sig", newline="") as input_file:
            rows = list(csv.DictReader(input_file))
        return normalize_input_rows(rows, source)
    if source_kind == "json":
        parsed = json.loads(path.read_text(encoding="utf-8-sig"))
        return normalize_input_rows(rows_from_json(parsed, source["path"]), source)
    raise RuntimeError(f"不支持的数据源类型：{source_kind}")


def validate_raw(item: dict[str, Any]) -> str:
    if not item.get("name"):
        return "缺少名称"
    if not GRADE_PATTERN.fullmatch(item.get("grade", "")):
        return f"等级无效：{item.get('grade', '')}"
    if not item.get("address"):
        return "缺少地址"
    if not item.get("sourceUrl"):
        return "缺少来源链接"
    if not item.get("publishedAt"):
        return "缺少来源发布日期"
    return ""


def stable_id(item: dict[str, Any]) -> str:
    prefix = {"上海市": "sh", "江苏省": "js", "浙江省": "zj"}.get(item["province"], "cn")
    value = f"{item['province']}|{item['name']}".encode("utf-8")
    digest = hashlib.sha256(value).hexdigest()[:14]
    return f"{prefix}-{digest}"


def read_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return fallback


def geocode(item: dict[str, Any], cache: dict[str, Any]) -> dict[str, Any]:
    cache_key = f"{item['province']}|{item.get('city', '')}|{item['address']}"
    if cache_key in cache:
        return cache[cache_key]
    if not AMAP_KEY:
        raise RuntimeError("缺少 AMAP_WEB_SERVICE_KEY，无法补充经纬度")

    query = urlencode(
        {
            "key": AMAP_KEY,
            "address": f"{item['province']}{item.get('city', '')}{item['address']}",
            "city": item.get("city") or item["province"],
        }
    )
    try:
        data = json.loads(fetch_text(f"https://restapi.amap.com/v3/geocode/geo?{query}"))
    except Exception as error:
        # 请求 URL 包含 Key，不把底层 URL 写入报告或终端。
        raise RuntimeError("高德地理编码请求失败") from error
    result = (data.get("geocodes") or [None])[0]
    if data.get("status") != "1" or not result or not result.get("location"):
        raise RuntimeError(f"高德未找到地址：{item['name']} / {item['address']}")

    lng, lat = (float(value) for value in result["location"].split(","))
    result_city = result.get("city")
    geocoded = {
        "lng": lng,
        "lat": lat,
        "province": result.get("province") or item["province"],
        "city": item.get("city", "") if isinstance(result_city, list) else result_city or item.get("city", ""),
        "district": result.get("district") or item.get("district", ""),
        "adcode": str(result.get("adcode") or ""),
    }
    cache[cache_key] = geocoded
    return geocoded


def apply_override(record: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
    if not override:
        return record
    next_level = override.get("level", record["level"])
    if isinstance(next_level, bool) or not isinstance(next_level, int) or not 0 <= next_level <= 9:
        raise RuntimeError(f"{record['id']} 的人工等级必须是 0–9 整数")

    return {
        **record,
        "level": next_level,
        "levelSource": record["levelSource"] if "level" not in override else "manual",
        "tags": list(dict.fromkeys([*record["tags"], *override.get("tags", [])])),
    }


def validate_record(record: dict[str, Any]) -> str:
    if not record.get("id") or not record.get("name"):
        return "缺少 ID 或名称"
    if not re.fullmatch(r"\d{6}", record.get("adcode", "")):
        return f"行政编码无效：{record.get('adcode', '')}"
    level = record.get("level")
    if isinstance(level, bool) or not isinstance(level, int) or not 0 <= level <= 9:
        return "等级必须是 0–9 整数"
    lng, lat = record.get("lng"), record.get("lat")
    if not isinstance(lng, (int, float)) or not isinstance(lat, (int, float)):
        return "坐标无效"
    if not math.isfinite(lng) or not math.isfinite(lat):
        return "坐标无效"
    if not 70 <= lng <= 140 or not 0 <= lat <= 60:
        return "坐标超出中国合理范围"
    official_tag = next((tag for tag in record.get("tags", []) if GRADE_PATTERN.fullmatch(tag)), None)
    if not official_tag:
        return "缺少官方 A 级标签"
    if record.get("levelSource") == "official" and int(official_tag[0]) != level:
        return "官方等级映射错误"
    return ""


def atomic_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(
        f"{json.dumps(value, ensure_ascii=False, indent=2)}\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def iso_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def main() -> int:
    sources = read_json(SOURCES_PATH, [])
    overrides = read_json(OVERRIDES_PATH, {})
    cache = read_json(CACHE_PATH, {})
    report: dict[str, Any] = {
        "generatedAt": iso_timestamp(),
        "sources": [],
        "rawCount": 0,
        "duplicateCount": 0,
        "generatedCount": 0,
        "errors": [],
        "overrides": [],
    }

    raw_items: list[dict[str, Any]] = []
    for source in sources:
        try:
            items = load_source(source)
            raw_items.extend(items)
            report["sources"].append(
                {"name": source["sourceName"], "kind": source["kind"], "count": len(items)}
            )
        except Exception as error:  # 生成报告需要汇总所有来源错误
            report["errors"].append({"source": source.get("sourceName", "未知来源"), "message": str(error)})
    report["rawCount"] = len(raw_items)

    unique: dict[str, dict[str, Any]] = {}
    for item in raw_items:
        problem = validate_raw(item)
        if problem:
            report["errors"].append({"item": item.get("name") or "未命名", "message": problem})
            continue
        item_id = stable_id(item)
        if item_id in unique:
            report["duplicateCount"] += 1
            continue
        unique[item_id] = {**item, "id": item_id}

    records: list[dict[str, Any]] = []
    if not AMAP_KEY and unique:
        report["errors"].append(
            {
                "source": "高德地理编码",
                "message": f"缺少 AMAP_WEB_SERVICE_KEY，{len(unique)} 条景区尚未补充经纬度",
            }
        )
    else:
        for item in unique.values():
            try:
                location = geocode(item, cache)
                record = {
                    "id": item["id"],
                    "name": item["name"],
                    "province": location["province"],
                    "city": location["city"],
                    "district": location["district"],
                    "adcode": location["adcode"],
                    "address": item["address"],
                    "lng": location["lng"],
                    "lat": location["lat"],
                    "level": int(item["grade"][0]),
                    "levelSource": "official",
                    "tags": [item["grade"]],
                    "source": {
                        "name": item["sourceName"],
                        "url": item["sourceUrl"],
                        "publishedAt": item["publishedAt"],
                    },
                }
                record = apply_override(record, overrides.get(item["id"]))
                if item["id"] in overrides:
                    report["overrides"].append(item["id"])
                problem = validate_record(record)
                if problem:
                    raise RuntimeError(problem)
                records.append(record)
            except Exception as error:  # 单条错误不应中止其余记录的校验
                report["errors"].append({"item": item["name"], "message": str(error)})

    records.sort(key=lambda record: (-record["level"], record["name"]))
    report["generatedCount"] = len(records)
    atomic_write(CACHE_PATH, cache)
    atomic_write(REPORT_PATH, report)

    if report["errors"]:
        print(f"生成失败：{len(report['errors'])} 个问题，详情见 {REPORT_PATH}", file=sys.stderr)
        return 1

    atomic_write(
        OUTPUT_PATH,
        {
            "schemaVersion": 1,
            "generatedAt": report["generatedAt"],
            "coordSystem": "GCJ-02",
            "coverage": ["上海市", "江苏省", "浙江省"],
            "items": records,
        },
    )
    print(f"已生成 {len(records)} 条景点数据：{OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
