#!/usr/bin/env python3
"""获取文化和旅游部 5A 景区与世界文化遗产阶段数据。"""

from __future__ import annotations

import argparse
from copy import deepcopy
import html as html_module
import json
import os
import re
import sys
import tempfile
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from difflib import SequenceMatcher
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen


TOOL_ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT_PATH = TOOL_ROOT / "tmp/01-文旅部景点数据.json"
DEFAULT_REPORT_PATH = TOOL_ROOT / "tmp/01-文旅部采集报告.json"

MCT_ORIGIN = "https://sjfw.mct.gov.cn"
FIVE_A_LIST_ENDPOINT = f"{MCT_ORIGIN}/api/spot/list/10"
HERITAGE_INDEX_URL = f"{MCT_ORIGIN}/site/dataservice/base?type=1"
DETAIL_ENDPOINT = f"{MCT_ORIGIN}/site/dataservice/culdetails"
REQUEST_HEADERS = {"User-Agent": "roam-attraction-source-fetcher/1.0"}
REQUEST_TIMEOUT_SECONDS = 60.0

OpenUrl = Callable[..., Any]
Timestamp = Callable[[], str]
Sleep = Callable[[float], None]


class DataContractError(RuntimeError):
    """文旅部响应或本地阶段数据不符合预期契约。"""


def iso_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output_file:
            json.dump(value, output_file, ensure_ascii=False, indent=2)
            output_file.write("\n")
            output_file.flush()
            os.fsync(output_file.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
        raise


def fetch_text(
    url: str,
    *,
    open_url: OpenUrl = urlopen,
    sleep: Sleep = time.sleep,
    attempts: int = 3,
) -> str:
    request = Request(url, headers=REQUEST_HEADERS)
    if attempts < 1:
        raise ValueError("attempts 必须至少为 1")
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with open_url(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                content = response.read()
                headers = getattr(response, "headers", None)
                charset = headers.get_content_charset() if headers is not None else None
            break
        except Exception as error:
            last_error = error
            if attempt + 1 < attempts:
                sleep(2**attempt)
    else:
        raise DataContractError(f"文化和旅游部数据请求失败（已尝试 {attempts} 次）") from last_error

    for encoding in (charset, "utf-8-sig", "utf-8"):
        if not encoding:
            continue
        try:
            return content.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    raise DataContractError("文化和旅游部响应编码无法识别")


def fetch_json(url: str, *, open_url: OpenUrl = urlopen) -> dict[str, Any]:
    text = fetch_text(url, open_url=open_url)
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise DataContractError("文化和旅游部 JSON 响应无法解析") from error
    if not isinstance(value, dict):
        raise DataContractError("文化和旅游部 JSON 顶层必须是对象")
    return value


def fetch_spot_list(
    category_id: int,
    *,
    open_url: OpenUrl = urlopen,
    page_size: int = 9999,
) -> tuple[list[dict[str, Any]], int]:
    query = urlencode({"page": 1, "pagesize": page_size})
    payload = fetch_json(
        f"{MCT_ORIGIN}/api/spot/list/{category_id}?{query}",
        open_url=open_url,
    )
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("list"), list):
        raise DataContractError("文旅部景点列表缺少 data.list")
    items = data["list"]
    if not all(isinstance(item, dict) for item in items):
        raise DataContractError("文旅部景点列表包含非对象记录")
    try:
        reported_total = int(data.get("total"))
    except (TypeError, ValueError) as error:
        raise DataContractError("文旅部景点列表总数无效") from error
    if reported_total != len(items):
        raise DataContractError(
            f"文旅部景点列表报告总数 {reported_total} 与实际解析数 {len(items)} 不一致"
        )
    if reported_total < 1:
        raise DataContractError("文旅部景点列表为空")
    return items, reported_total


def clean_text(value: str) -> str:
    decoded = html_module.unescape(value).replace("\xa0", " ")
    return re.sub(r"\s+", " ", decoded).strip()


class DetailPageParser(HTMLParser):
    """只提取详情页中带稳定 class 的文本和图片。"""

    TARGET_CLASSES = {"title", "linelimit", "lines", "content"}
    VOID_TAGS = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.active: list[dict[str, Any]] = []
        self.values: dict[str, list[str]] = {name: [] for name in self.TARGET_CLASSES}
        self.image_src = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in self.VOID_TAGS:
            self.depth += 1
        attributes = {name: value or "" for name, value in attrs}
        classes = set(attributes.get("class", "").split())
        if tag == "img" and "image" in classes and not self.image_src:
            self.image_src = attributes.get("src", "")
        for target in self.TARGET_CLASSES & classes:
            self.active.append({"target": target, "depth": self.depth, "parts": []})
        if tag in {"br", "p"}:
            for capture in self.active:
                if capture["target"] == "content" and capture["parts"]:
                    capture["parts"].append(" ")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        for capture in self.active:
            capture["parts"].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in self.VOID_TAGS:
            return
        closing = [capture for capture in self.active if capture["depth"] == self.depth]
        for capture in closing:
            value = clean_text("".join(capture["parts"]))
            if value:
                self.values[capture["target"]].append(value)
            self.active.remove(capture)
        self.depth = max(0, self.depth - 1)


def split_label(value: str) -> tuple[str, str]:
    parts = re.split(r"[：:]", value, maxsplit=1)
    if len(parts) != 2:
        return "", clean_text(value)
    return clean_text(parts[0]), clean_text(parts[1])


def first_year(value: str) -> str:
    match = re.search(r"(?:19|20)\d{2}", value)
    return match.group(0) if match else ""


def parse_detail_page(html: str, *, category_id: int, detail_url: str) -> dict[str, Any]:
    parser = DetailPageParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception as error:
        raise DataContractError("文旅部详情页 HTML 无法解析") from error

    titles = parser.values["title"]
    if not titles:
        raise DataContractError("文旅部详情页缺少标题")
    title = titles[0]
    expected_prefix = "5A级旅游景区" if category_id == 10 else "世界文化遗产"
    title_match = re.fullmatch(
        rf"{re.escape(expected_prefix)}\s*[-－—]\s*(.+)",
        title,
    )
    if title_match is None:
        raise DataContractError("文旅部详情页分类或标题格式不匹配")
    name = clean_text(title_match.group(1))
    if not name:
        raise DataContractError("文旅部详情页名称为空")

    region_label, region_text = ("", "")
    if parser.values["linelimit"]:
        region_label, region_text = split_label(parser.values["linelimit"][0])

    labelled_lines = [split_label(value) for value in parser.values["lines"]]
    year_label = ""
    year_text = ""
    extended_year_text = ""
    for label, value in labelled_lines:
        if label in {"评定年份", "列入时间", "登录年份"} and not year_text:
            year_label, year_text = label, value
        elif label == "扩展时间" and not extended_year_text:
            extended_year_text = value

    if not region_text:
        raise DataContractError(f"文旅部详情页缺少地区信息：{name}")
    if not year_text:
        raise DataContractError(f"文旅部详情页缺少年份信息：{name}")

    description = " ".join(parser.values["content"])
    heritage_type = "mixed" if "文化和自然混合遗产" in f"{title} {name}" else "cultural"
    return {
        "name": name,
        "regionLabel": region_label,
        "regionText": region_text,
        "yearLabel": year_label,
        "yearText": year_text,
        "year": first_year(year_text),
        "extendedYearText": extended_year_text,
        "extendedYear": first_year(extended_year_text),
        "description": description,
        "officialImageUrl": urljoin(detail_url, parser.image_src) if parser.image_src else "",
        "heritageType": heritage_type,
    }


def parse_heritage_count(html: str) -> int:
    match = re.search(r"共\s*(\d+)\s*个", clean_text(re.sub(r"<[^>]+>", " ", html)))
    if not match:
        raise DataContractError("世界文化遗产页面缺少报告总数")
    count = int(match.group(1))
    if not 1 <= count <= 500:
        raise DataContractError(f"世界文化遗产报告总数异常：{count}")
    return count


PROVINCE_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("北京市", ("北京市", "北京")),
    ("天津市", ("天津市", "天津")),
    ("河北省", ("河北省", "河北")),
    ("山西省", ("山西省", "山西")),
    ("内蒙古自治区", ("内蒙古自治区", "内蒙古")),
    ("辽宁省", ("辽宁省", "辽宁")),
    ("吉林省", ("吉林省", "吉林")),
    ("黑龙江省", ("黑龙江省", "黑龙江")),
    ("上海市", ("上海市", "上海")),
    ("江苏省", ("江苏省", "江苏")),
    ("浙江省", ("浙江省", "浙江")),
    ("安徽省", ("安徽省", "安徽")),
    ("福建省", ("福建省", "福建")),
    ("江西省", ("江西省", "江西")),
    ("山东省", ("山东省", "山东")),
    ("河南省", ("河南省", "河南")),
    ("湖北省", ("湖北省", "湖北")),
    ("湖南省", ("湖南省", "湖南")),
    ("广东省", ("广东省", "广东")),
    ("广西壮族自治区", ("广西壮族自治区", "广西")),
    ("海南省", ("海南省", "海南")),
    ("重庆市", ("重庆市", "重庆")),
    ("四川省", ("四川省", "四川")),
    ("贵州省", ("贵州省", "贵州")),
    ("云南省", ("云南省", "云南")),
    ("西藏自治区", ("西藏自治区", "西藏")),
    ("陕西省", ("陕西省", "陕西")),
    ("甘肃省", ("甘肃省", "甘肃")),
    ("青海省", ("青海省", "青海")),
    ("宁夏回族自治区", ("宁夏回族自治区", "宁夏")),
    ("新疆维吾尔自治区", ("新疆维吾尔自治区", "新疆生产建设兵团", "新疆", "兵团")),
    ("台湾省", ("台湾省", "台湾")),
    ("香港特别行政区", ("香港特别行政区", "香港")),
    ("澳门特别行政区", ("澳门特别行政区", "澳门")),
)


def province_hints(*structured_values: str, names: tuple[str, ...] = ()) -> list[str]:
    """提取省级地区；自由名称中只匹配全称，避免“庐山西海”误命中“山西”。"""

    structured = " ".join(value for value in structured_values if value)
    free_text = " ".join(value for value in names if value)
    found: list[tuple[int, str]] = []
    for canonical, aliases in PROVINCE_ALIASES:
        positions = [structured.find(alias) for alias in aliases if alias in structured]
        if canonical in free_text:
            positions.append(len(structured) + 1 + free_text.find(canonical))
        if positions:
            found.append((min(positions), canonical))
    return [canonical for _, canonical in sorted(found)]


def is_multi_location(region_text: str, hints: list[str]) -> bool:
    if len(hints) != 1:
        return True
    # 遗产分布中的枚举通常意味着多个独立地点；普通完整地址不会包含这些分隔符。
    return bool(re.search(r"[、；;]", region_text))


def heritage_name_suggests_multiple_locations(name: str) -> bool:
    compact = normalize_name(name)
    if "、" in compact or "（含" in compact or "(含" in compact:
        return True
    return any(
        marker in compact
        for marker in (
            "及其周围",
            "及周围",
            "峨眉山—乐山大佛",
            "峨眉山-乐山大佛",
            "皖南古村落—西递",
            "皖南古村落-西递",
            "青城山—都江堰",
            "青城山-都江堰",
            "苏州古典园林",
        )
    )


def detail_url(item_id: int, category_id: int) -> str:
    return f"{DETAIL_ENDPOINT}?{urlencode({'id': item_id, 'curId': category_id})}"


def published_date(value: Any) -> str:
    match = re.search(r"(?:19|20)\d{2}-\d{2}-\d{2}", str(value or ""))
    return match.group(0) if match else ""


def validate_five_a_list_detail(
    list_item: dict[str, Any],
    detail: dict[str, Any],
) -> list[dict[str, str]]:
    list_name = clean_text(str(list_item.get("name") or ""))
    list_year_text = clean_text(str(list_item.get("year") or ""))
    if not list_name or not list_year_text:
        raise DataContractError("5A 列表记录缺少名称或年份")
    if list_name.endswith(list_year_text):
        list_name = list_name[: -len(list_year_text)]
    discrepancies: list[dict[str, str]] = []
    if normalize_name(list_name) != normalize_name(str(detail.get("name") or "")):
        discrepancies.append(
            {
                "field": "name",
                "listValue": list_name,
                "detailValue": str(detail.get("name") or ""),
                "message": "文化和旅游部列表与详情页名称不一致",
            }
        )
    detail_year_text = str(detail.get("yearText") or "")
    if first_year(list_year_text) != str(detail.get("year") or ""):
        discrepancies.append(
            {
                "field": "classificationYear",
                "listValue": list_year_text,
                "detailValue": detail_year_text,
                "message": "文化和旅游部列表与详情页评定年份不一致",
            }
        )
    return discrepancies


def build_five_a_record(list_item: dict[str, Any], detail: dict[str, Any]) -> dict[str, Any]:
    try:
        source_id = int(list_item["id"])
    except (KeyError, TypeError, ValueError) as error:
        raise DataContractError("5A 列表记录缺少有效 ID") from error
    list_year_text = clean_text(str(list_item.get("year") or ""))
    name = clean_text(str(list_item.get("name") or ""))
    if list_year_text and name.endswith(list_year_text):
        name = name[: -len(list_year_text)]
    detail_name = detail["name"]
    hints = province_hints(
        detail.get("regionText", ""),
        str(list_item.get("provinceName") or ""),
        names=(name, detail_name),
    )
    source_discrepancies = validate_five_a_list_detail(list_item, detail)
    review_reasons: list[str] = []
    if is_multi_location(detail["regionText"], hints):
        review_reasons.append("跨省、多地点或无法确定唯一位置")
    needs_review = bool(review_reasons)
    record = {
        "id": f"mct-5a-{source_id}",
        "name": name,
        "detailName": detail_name,
        "classifications": [
            {
                "type": "a-level",
                "value": "5A",
                "year": detail.get("year", ""),
                "yearText": detail.get("yearText", ""),
            }
        ],
        "regionText": detail["regionText"],
        "provinceHints": hints,
        "description": detail.get("description", ""),
        "officialImageUrl": detail.get("officialImageUrl", ""),
        "geocodeQuery": "" if needs_review else f"{hints[0]}{name}",
        "reviewStatus": "needs_review" if needs_review else "ready",
        "reviewReason": "；".join(review_reasons),
        "sources": [
            {
                "name": "文化和旅游部",
                "url": detail_url(source_id, 10),
                "sourceId": str(source_id),
                "publishedAt": "",
                "sourceRecordCreatedAt": published_date(list_item.get("created_at")),
            }
        ],
    }
    if source_discrepancies:
        record["sourceDiscrepancies"] = source_discrepancies
    return record


DISTRIBUTED_HERITAGE_NAMES_BY_ID = {
    14: "平遥古城",
    16: "丽江古城",
    19: "大足石刻",
    27: "澳门历史城区",
    29: "开平碉楼与村落",
    31: "五台山",
    32: "登封“天地之中” 历史建筑群",
    35: "红河哈尼梯田文化景观",
    39: "左江花山岩画文化景观",
    41: "良渚古城遗址",
    42: "泉州：宋元中国的世界海洋商贸中心",
    43: "普洱景迈山古茶林文化景观",
    44: "北京中轴线——中国理想都城秩序的杰作",
}


def build_heritage_record(source_id: int, detail: dict[str, Any]) -> dict[str, Any]:
    name = detail["name"]
    hints = province_hints(detail.get("regionText", ""), names=(name,))
    review_reasons: list[str] = []
    if is_multi_location(detail["regionText"], hints):
        review_reasons.append("跨省、多地点或无法确定唯一位置")
    if heritage_name_suggests_multiple_locations(name):
        review_reasons.append("遗产名称包含多个可独立定位的组成部分")
    expected_distributed_name = DISTRIBUTED_HERITAGE_NAMES_BY_ID.get(source_id)
    if expected_distributed_name is not None:
        if normalize_name(name) != normalize_name(expected_distributed_name):
            raise DataContractError(
                f"世界文化遗产详情 ID {source_id} 与多组成部分审计表名称不一致"
            )
        review_reasons.append("官方资料显示该遗产由多个可独立定位的组成部分构成")
    needs_review = bool(review_reasons)
    heritage_type = detail.get("heritageType") or (
        "mixed" if "文化和自然混合遗产" in name else "cultural"
    )
    classification: dict[str, str] = {
        "type": "world-heritage",
        "value": heritage_type,
        "year": detail.get("year", ""),
        "yearText": detail.get("yearText", ""),
    }
    if detail.get("extendedYear") or detail.get("extendedYearText"):
        classification["extendedYear"] = detail.get("extendedYear", "")
        classification["extendedYearText"] = detail.get("extendedYearText", "")
    return {
        "id": f"mct-world-heritage-{source_id}",
        "name": name,
        "classifications": [classification],
        "regionText": detail["regionText"],
        "provinceHints": hints,
        "description": detail.get("description", ""),
        "officialImageUrl": detail.get("officialImageUrl", ""),
        "geocodeQuery": "" if needs_review else f"{hints[0]}{name}",
        "reviewStatus": "needs_review" if needs_review else "ready",
        "reviewReason": "；".join(dict.fromkeys(review_reasons)),
        "sources": [
            {
                "name": "文化和旅游部",
                "url": detail_url(source_id, 4),
                "sourceId": str(source_id),
                "publishedAt": "",
            }
        ],
    }


def normalize_name(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).strip()


def unique_official_names(items: list[dict[str, Any]], *, label: str) -> set[str]:
    names: set[str] = set()
    for item in items:
        name = item.get("name")
        if not isinstance(name, str) or not normalize_name(name):
            raise DataContractError(f"{label}列表包含空名称")
        names.add(normalize_name(name))
    return names


def records_are_compatible(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_hints = set(left.get("provinceHints") or [])
    right_hints = set(right.get("provinceHints") or [])
    return bool(left_hints and right_hints and left_hints & right_hints)


def add_review_reason(record: dict[str, Any], reason: str) -> None:
    existing = str(record.get("reviewReason") or "").strip()
    reasons = [part for part in existing.split("；") if part]
    if reason not in reasons:
        reasons.append(reason)
    record["reviewStatus"] = "needs_review"
    record["reviewReason"] = "；".join(reasons)
    record["geocodeQuery"] = ""


GENERIC_COMPARISON_TERMS = (
    "风景名胜",
    "旅游",
    "文化",
    "风景",
    "国家",
    "历史",
    "建筑",
    "景观",
    "景区",
    "公园",
)


def comparison_name(value: str) -> str:
    text = unicodedata.normalize("NFKC", value)
    text = re.sub(r"[（(][^）)]*[）)]", "", text)
    text = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]+", "", text)
    division = re.compile(r"^[\u4e00-\u9fff]{2,8}?(?:自治州|地区|市|盟|县|区)")
    for _ in range(3):
        match = division.match(text)
        if match is None or len(text) - match.end() < 2:
            break
        text = text[match.end() :]
    for term in GENERIC_COMPARISON_TERMS:
        text = text.replace(term, "")
    text = re.sub(r"区$", "", text)
    return text


def names_suggest_same_attraction(left: str, right: str) -> bool:
    left_core = comparison_name(left)
    right_core = comparison_name(right)
    if not left_core or not right_core:
        return False
    if left_core == right_core and len(left_core) >= 2:
        return True
    shorter, longer = sorted((left_core, right_core), key=len)
    if len(shorter) >= 3 and shorter in longer:
        return True
    longest = SequenceMatcher(None, left_core, right_core, autojunk=False).find_longest_match()
    if longest.size >= 3:
        return True

    special_aliases = (
        ("三孔", ("孔庙", "孔林", "孔府")),
        ("苏州园林", ("苏州古典园林",)),
        ("西湖", ("杭州西湖",)),
        ("天坛", ("北京皇家祭坛天坛",)),
    )
    return any(
        (
            short_name in left_core
            and all(part in right_core for part in expanded)
        )
        or (
            short_name in right_core
            and all(part in left_core for part in expanded)
        )
        for short_name, expanded in special_aliases
    )


HERITAGE_COMPONENT_AUDIT: dict[str, tuple[str, dict[str, str]]] = {
    "mct-world-heritage-1": (
        "长城",
        {
            "mct-5a-34783": "八达岭—慕田峪长城旅游区",
            "mct-5a-34801": "承德市金山岭长城景区",
            "mct-5a-34799": "秦皇岛市山海关景区",
            "mct-5a-35103": "嘉峪关市嘉峪关文物景区",
            "mct-5a-34809": "忻州市雁门关景区",
        },
    ),
    "mct-world-heritage-2": (
        "明清皇宫（北京故宫、沈阳故宫）",
        {"mct-5a-34780": "故宫博物院"},
    ),
    "mct-world-heritage-9": (
        "拉萨布达拉宫历史建筑群（含罗布林卡和大昭寺）",
        {"mct-5a-35085": "拉萨市大昭寺景区"},
    ),
    "mct-world-heritage-22": (
        "明清皇家陵寝",
        {
            "mct-5a-34987": "钟祥市明显陵文化旅游景区",
            "mct-5a-34795": "唐山市清东陵景区",
            "mct-5a-34800": "保定市清西陵景区",
            "mct-5a-34784": "明十三陵景区",
            "mct-5a-34854": "南京市钟山风景名胜区－中山陵园风景区",
        },
    ),
    "mct-world-heritage-26": (
        "高句丽王城、王陵及贵族墓葬",
        {"mct-5a-34830": "本溪市五女山景区"},
    ),
    "mct-world-heritage-32": (
        "登封“天地之中” 历史建筑群",
        {"mct-5a-34956": "郑州市嵩山少林景区"},
    ),
    "mct-world-heritage-36": (
        "长安-天山廊道的路网",
        {
            "mct-5a-35092": "西安市大雁塔－大唐芙蓉园景区",
            "mct-5a-35099": "西安市大明宫旅游景区",
            "mct-5a-35105": "天水市麦积山景区",
            "mct-5a-35108": "临夏州炳灵寺世界文化遗产旅游区",
        },
    ),
    "mct-world-heritage-37": (
        "大运河",
        {
            "mct-5a-34946": "枣庄市台儿庄古城景区",
            "mct-5a-34889": "湖州市南浔古镇景区",
        },
    ),
    "mct-world-heritage-42": (
        "泉州：宋元中国的世界海洋商贸中心",
        {"mct-5a-34918": "泉州市清源山景区"},
    ),
    "mct-world-heritage-44": (
        "北京中轴线——中国理想都城秩序的杰作",
        {
            "mct-5a-34780": "故宫博物院",
            "mct-5a-34781": "天坛公园",
        },
    ),
}


def mark_cross_classification_relationships(
    records: list[dict[str, Any]],
) -> list[dict[str, str]]:
    five_a_records = [
        record
        for record in records
        if any(value.get("type") == "a-level" for value in record.get("classifications", []))
        and not any(
            value.get("type") == "world-heritage"
            for value in record.get("classifications", [])
        )
    ]
    heritage_records = [
        record
        for record in records
        if any(
            value.get("type") == "world-heritage"
            for value in record.get("classifications", [])
        )
        and not any(value.get("type") == "a-level" for value in record.get("classifications", []))
    ]

    initially_reviewed_heritage_ids = {
        record["id"]
        for record in heritage_records
        if record.get("reviewStatus") == "needs_review"
    }
    pairs: list[dict[str, str]] = []
    pair_keys: set[tuple[str, str]] = set()

    def add_pair(
        five_a: dict[str, Any],
        heritage: dict[str, Any],
        *,
        relationship: str,
    ) -> None:
        key = (five_a["id"], heritage["id"])
        if key in pair_keys:
            return
        pair_keys.add(key)
        if relationship == "possible_same_object":
            five_a.setdefault("possibleDuplicateIds", []).append(heritage["id"])
            heritage.setdefault("possibleDuplicateIds", []).append(five_a["id"])
            add_review_reason(five_a, "疑似与世界遗产记录指向同一对象，需人工决定是否合并")
            add_review_reason(heritage, "疑似与 5A 景区记录指向同一对象，需人工决定是否合并")
        else:
            heritage.setdefault("possibleComponentIds", []).append(five_a["id"])
            add_review_reason(heritage, "与一个或多个 5A 景区存在组成部分重叠，需人工拆分或保留代表点")
        pairs.append(
            {
                "fiveAId": five_a["id"],
                "fiveAName": five_a["name"],
                "worldHeritageId": heritage["id"],
                "worldHeritageName": heritage["name"],
                "relationship": relationship,
            }
        )

    for five_a in five_a_records:
        for heritage in heritage_records:
            if not records_are_compatible(five_a, heritage):
                continue
            if not names_suggest_same_attraction(five_a["name"], heritage["name"]):
                continue
            add_pair(
                five_a,
                heritage,
                relationship=(
                    "component_overlap"
                    if heritage["id"] in initially_reviewed_heritage_ids
                    else "possible_same_object"
                ),
            )

    by_id = {record["id"]: record for record in records}
    for heritage_id, (expected_heritage_name, five_a_items) in HERITAGE_COMPONENT_AUDIT.items():
        heritage = by_id.get(heritage_id)
        if heritage is None:
            continue
        if normalize_name(heritage["name"]) != normalize_name(expected_heritage_name):
            raise DataContractError(f"{heritage_id} 与组成部分审计表名称不一致")
        for five_a_id, expected_five_a_name in five_a_items.items():
            five_a = by_id.get(five_a_id)
            if five_a is not None:
                audited_names = {
                    normalize_name(str(five_a.get("name") or "")),
                    normalize_name(str(five_a.get("detailName") or "")),
                }
                if normalize_name(expected_five_a_name) not in audited_names:
                    raise DataContractError(f"{five_a_id} 与组成部分审计表名称不一致")
                add_pair(five_a, heritage, relationship="component_overlap")
    return pairs


def merge_exact_records(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    merged: list[dict[str, Any]] = []
    merge_count = 0
    for record in records:
        match = next(
            (
                current
                for current in merged
                if normalize_name(current["name"]) == normalize_name(record["name"])
                and records_are_compatible(current, record)
            ),
            None,
        )
        if match is None:
            merged.append({**record})
            continue

        merge_count += 1
        match["sourceRecordIds"] = list(dict.fromkeys([
            *match.get("sourceRecordIds", [match["id"]]),
            *record.get("sourceRecordIds", [record["id"]]),
        ]))
        def stable_id_key(value: str) -> tuple[str, int, int | str]:
            id_match = re.fullmatch(r"(.+?)(\d+)", value)
            if id_match:
                return id_match.group(1), 0, int(id_match.group(2))
            return value, 1, value

        match["id"] = min(
            (str(match["id"]), str(record["id"])),
            key=stable_id_key,
        )
        region_texts = list(
            dict.fromkeys(
                value
                for value in (
                    *match.get("regionTexts", []),
                    match.get("regionText", ""),
                    *record.get("regionTexts", []),
                    record.get("regionText", ""),
                )
                if value
            )
        )
        if len(region_texts) > 1:
            match["regionTexts"] = region_texts
            match["regionText"] = "；".join(region_texts)
        classification_keys = {
            json.dumps(value, ensure_ascii=False, sort_keys=True)
            for value in match.get("classifications", [])
        }
        for value in record.get("classifications", []):
            key = json.dumps(value, ensure_ascii=False, sort_keys=True)
            if key not in classification_keys:
                match.setdefault("classifications", []).append(value)
                classification_keys.add(key)

        source_keys = {
            (source.get("url"), source.get("sourceId")) for source in match.get("sources", [])
        }
        for source in record.get("sources", []):
            key = (source.get("url"), source.get("sourceId"))
            if key not in source_keys:
                match.setdefault("sources", []).append(source)
                source_keys.add(key)

        match["provinceHints"] = list(
            dict.fromkeys([*match.get("provinceHints", []), *record.get("provinceHints", [])])
        )
        if record.get("reviewStatus") == "needs_review":
            record_reason = str(record.get("reviewReason") or "需要人工审核")
            for reason in record_reason.split("；"):
                if reason:
                    add_review_reason(match, reason)
        if not match.get("description") and record.get("description"):
            match["description"] = record["description"]
        if not match.get("officialImageUrl") and record.get("officialImageUrl"):
            match["officialImageUrl"] = record["officialImageUrl"]
    return merged, merge_count


# 用户已确认这些记录是同一景点；固定 ID 和名称，不能把模糊名称匹配当作合并授权。
CONFIRMED_SAME_ATTRACTIONS = (
    ("mct-5a-35089", "西安市秦始皇帝陵博物院景区", 3, "秦始皇陵及兵马俑"),
    ("mct-5a-34976", "十堰市武当山风景区", 8, "武当山古建筑群"),
    ("mct-5a-34959", "安阳市殷墟景区", 28, "殷墟"),
    ("mct-5a-34957", "洛阳市龙门石窟景区", 23, "龙门石窟"),
    ("mct-5a-34925", "九江市庐山市庐山风景名胜区", 12, "庐山国家公园"),
    ("mct-5a-34913", "厦门市鼓浪屿风景名胜区", 40, "鼓浪屿：历史国际社区"),
    ("mct-5a-34900", "黄山市黄山风景区", 7, "黄山（文化和自然混合遗产）"),
    ("mct-5a-34878", "杭州市杭州西湖风景区", 33, "杭州西湖文化景观"),
    ("mct-5a-34804", "大同市云冈石窟景区", 25, "云冈石窟"),
    ("mct-5a-34782", "颐和园", 17, "北京皇家园林—颐和园"),
    ("mct-5a-34781", "天坛公园", 18, "北京皇家祭坛—天坛"),
)


def merge_confirmed_records(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    by_id = {record["id"]: record for record in records}
    removed: set[str] = set()
    for five_a_id, five_a_name, heritage_number, heritage_name in CONFIRMED_SAME_ATTRACTIONS:
        heritage_id = f"mct-world-heritage-{heritage_number}"
        five_a, heritage = by_id.get(five_a_id), by_id.get(heritage_id)
        if five_a is None or heritage is None:
            continue
        if (
            normalize_name(five_a["name"]) != normalize_name(five_a_name)
            or normalize_name(heritage["name"]) != normalize_name(heritage_name)
            or not records_are_compatible(five_a, heritage)
        ):
            raise DataContractError(f"{five_a_id} / {heritage_id} 与已确认合并记录不一致")
        originals = [deepcopy(five_a), deepcopy(heritage)]
        for field in ("classifications", "sources", "provinceHints", "sourceDiscrepancies", "sourceRecordIds"):
            values = list(five_a.get(field, []))
            for value in heritage.get(field, []):
                if value not in values:
                    values.append(value)
            if values:
                five_a[field] = values
        five_a["aliases"] = list(dict.fromkeys([five_a["name"], heritage["name"]]))
        five_a["mergedSourceIds"] = [five_a_id, heritage_id]
        five_a["sourceRecords"] = originals
        five_a["mergeReason"] = "用户确认同一景点，合并并保留全部分类身份和来源"
        removed.add(heritage_id)
    return [record for record in records if record["id"] not in removed], len(removed)


# 只拆官方资料确认的独立游览地，不按标点自动拆分园区内部设施。
# 子项的短标识固定，修改展示名称或查询名称不会改变景点 ID。
FIVE_A_SPLITS = {
    35005: ("观澜湖", "广东省深圳市龙华区", (("eco-park", "观澜湖生态运动公社"),)),
    34959: ("殷墟", "河南省安阳市殷都区", (
        ("palace", "殷墟宫殿宗庙遗址"), ("royal-tombs", "殷墟王陵遗址"))),
    34921: ("古田", "福建省龙岩市上杭县", (
        ("meeting-site", "古田会议会址"), ("memorial", "古田会议纪念馆"), ("mao-garden", "毛主席纪念园"))),
    34859: ("环球恐龙城", "江苏省常州市新北区", (("dinosaur-park", "中华恐龙园"),)),
    35036: ("喀斯特", "重庆市武隆区", (
        ("bridges", "天生三桥景区"), ("fairy-mountain", "仙女山国家森林公园"), ("furong-cave", "芙蓉洞"))),
    35035: ("小三峡", "重庆市巫山县", (
        ("small-gorges", "巫山小三峡"), ("mini-gorges", "小小三峡"))),
    34965: ("恐龙", "河南省南阳市西峡县", (
        ("dinosaur", "中国西峡恐龙遗迹园"), ("laojieling", "老界岭旅游度假风景区"))),
    35109: ("官鹅沟", "甘肃省陇南市宕昌县", (
        ("guanzhu", "官珠沟景区"), ("eman", "鹅嫚沟景区"))),
    35108: ("炳灵寺", "甘肃省临夏回族自治州永靖县", (
        ("grottoes", "炳灵寺石窟"), ("stone-forest", "炳灵石林"))),
    35098: ("延安革命", "陕西省延安市宝塔区", (
        ("baota", "宝塔山景区"), ("zaoyuan", "枣园革命旧址"),
        ("yangjialing", "杨家岭革命旧址"), ("memorial", "延安革命纪念馆"))),
    35081: ("火山热海", "云南省保山市腾冲市", (
        ("volcano", "腾冲火山景区（马站火山群）"), ("hot-spring", "腾冲热海风景区"))),
    35071: ("赤水丹霞", "贵州省遵义市赤水市", (
        ("waterfall", "赤水大瀑布"), ("foguangyan", "佛光岩景区"), ("yanziyan", "燕子岩景区"))),
    35067: ("樟江", "贵州省黔南布依族苗族自治州荔波县", (
        ("xiaoqikong", "小七孔景区"), ("daqikong", "大七孔景区"), ("shuichun", "水春河景区"))),
    35038: ("黑山谷", "重庆市万盛经济技术开发区", (
        ("black-valley", "黑山谷景区"), ("stone-sea", "龙鳞石海景区"))),
    35014: ("星湖", "广东省肇庆市", (
        ("seven-stars", "七星岩景区"), ("dinghu", "鼎湖山景区"))),
    35013: ("惠州西湖", "广东省惠州市惠城区", (
        ("west-lake", "惠州西湖"), ("honghua", "红花湖景区"))),
    34993: ("岳麓山", "湖南省长沙市", (
        ("yuelu", "岳麓山景区"), ("orange-island", "橘子洲景区"))),
    34987: ("明显陵", "湖北省荆门市钟祥市", (
        ("xianling", "明显陵"), ("mochou", "莫愁村"))),
    34981: ("木兰", "湖北省武汉市黄陂区", (
        ("mountain", "木兰山景区"), ("lake", "木兰天池"),
        ("grassland", "木兰草原"), ("cloud-mountain", "木兰云雾山"))),
    34954: ("奥帆", "山东省青岛市", (
        ("sailing", "青岛奥林匹克帆船中心"), ("underwater", "青岛海底世界"))),
    34948: ("沂蒙山", "山东省", (
        ("yishan", "沂山风景区", "山东省潍坊市临朐县"),
        ("guimeng", "蒙山龟蒙景区", "山东省临沂市平邑县"),
        ("yunmeng", "蒙山云蒙景区", "山东省临沂市蒙阴县"))),
    34941: ("蓬莱阁", "山东省烟台市蓬莱区", (
        ("pavilion", "蓬莱阁景区"), ("three-mountains", "三仙山风景区"), ("eight-immortals", "八仙过海景区"))),
    34937: ("庐山西海", "江西省九江市", (
        ("west-sea", "庐山西海风景区"), ("yunju", "云居山景区"))),
    34931: ("共和国摇篮", "江西省赣州市瑞金市", (
        ("yeping", "叶坪革命旧址群"), ("red-well", "红井景区"),
        ("ersuda", "二苏大景区"), ("memorial", "中华苏维埃纪念园"))),
    34918: ("清源山", "福建省泉州市", (
        ("mountain", "清源山景区（老君岩）"), ("saints", "伊斯兰教圣墓"), ("jiuri", "九日山祈风石刻"))),
    34907: ("古徽州", "安徽省黄山市", (
        ("ancient-city", "徽州古城"), ("tangyue", "棠樾牌坊群鲍家花园"),
        ("tangmo", "唐模古村"), ("qiankou", "潜口民宅"), ("chengkan", "呈坎"))),
    34896: ("刘伯温", "浙江省温州市文成县", (
        ("temple", "刘基庙"), ("waterfall", "百丈漈景区"))),
    34884: ("横店", "浙江省金华市东阳市横店镇", (
        ("guangzhou-hongkong", "广州街香港街景区"), ("qin-palace", "秦王宫景区"),
        ("riverside", "清明上河图景区"), ("imperial-palace", "明清宫苑景区"), ("dream-valley", "梦幻谷景区"))),
    34870: ("周恩来", "江苏省淮安市淮安区", (
        ("residence", "周恩来故居"), ("memorial", "周恩来纪念馆"), ("hexia", "河下古镇"))),
    34855: ("三国水浒城", "江苏省无锡市滨湖区", (
        ("three-kingdoms", "三国城"), ("water-margin", "水浒城"))),
    34851: ("西沙明珠湖", "上海市崇明区", (
        ("wetland", "西沙湿地"), ("lake", "明珠湖公园"))),
    34818: ("中俄边境", "内蒙古自治区呼伦贝尔市满洲里市", (
        ("gate", "满洲里国门景区"), ("matryoshka", "满洲里套娃景区"), ("tower", "北疆明珠观光塔"))),
    35138: ("三五九旅", "新疆维吾尔自治区阿拉尔市", (
        ("memorial", "三五九旅屯垦纪念馆"), ("desert", "沙漠之门景区"))),
    35103: ("嘉峪关", "甘肃省嘉峪关市", (
        ("fortress", "嘉峪关关城"), ("overhanging", "悬壁长城"), ("first-beacon", "长城第一墩"))),
    35097: ("城墙", "陕西省西安市", (
        ("wall", "西安城墙"), ("steles", "西安碑林博物馆"))),
    35092: ("大雁塔", "陕西省西安市", (
        ("pagoda", "大雁塔大慈恩寺"), ("furong", "大唐芙蓉园"))),
    35046: ("青城山", "四川省成都市都江堰市", (
        ("qingcheng", "青城山景区"), ("dujiangyan", "都江堰景区"))),
    35034: ("大足石刻", "重庆市大足区", (
        ("baodingshan", "宝顶山石刻"), ("beishan", "北山石刻"))),
    35020: ("两江四湖", "广西壮族自治区桂林市", (
        ("waterways", "两江四湖景区"), ("elephant", "象鼻山景区"))),
    35015: ("开平碉楼", "广东省江门市开平市", (
        ("zili", "自力村碉楼群"), ("majianglong", "马降龙碉楼群"), ("liyuan", "立园"))),
    34999: ("矮寨", "湖南省湘西土家族苗族自治州", (
        ("aizhai", "矮寨大桥景区"), ("shibadong", "十八洞村"), ("dehang", "德夯大峡谷"))),
    34992: ("岳阳楼", "湖南省岳阳市", (
        ("tower", "岳阳楼景区"), ("island", "君山岛景区"))),
    34989: ("武陵源", "湖南省张家界市", (
        ("wulingyuan", "武陵源景区"), ("tianmen", "天门山国家森林公园"))),
    34974: ("三峡大坝", "湖北省宜昌市", (
        ("dam", "三峡大坝旅游区"), ("quyuan", "屈原故里文化旅游区"))),
    34967: ("红旗渠", "河南省安阳市林州市", (
        ("canal", "红旗渠景区"), ("canyon", "太行大峡谷景区"))),
    34963: ("老君山", "河南省洛阳市栾川县", (
        ("laojun", "老君山景区"), ("jiguan", "鸡冠洞景区"))),
    34962: ("尧山", "河南省平顶山市鲁山县", (
        ("yaoshan", "尧山风景区"), ("buddha", "中原大佛景区"))),
    34958: ("云台山", "河南省焦作市", (
        ("yuntai", "云台山景区"), ("shennong", "神农山景区"), ("qingtian", "青天河景区"))),
    34952: ("萤火虫", "山东省临沂市沂水县", (
        ("firefly", "萤火虫水洞"), ("canyon", "山东地下大峡谷"))),
    34942: ("三孔", "山东省济宁市曲阜市", (
        ("temple", "曲阜孔庙"), ("cemetery", "孔林"), ("mansion", "孔府"))),
    34917: ("白水洋", "福建省宁德市屏南县", (
        ("baishuiyang", "白水洋景区"), ("yuanyangxi", "鸳鸯溪景区"))),
    34916: ("福建土楼", "", (
        ("hongkeng", "洪坑土楼群", "福建省龙岩市永定区"),
        ("gaobei", "高北土楼群", "福建省龙岩市永定区"),
        ("tianluokeng", "田螺坑土楼群", "福建省漳州市南靖县"),
        ("yunshuiyao", "云水谣古镇", "福建省漳州市南靖县"))),
    34903: ("西递", "安徽省黄山市黟县", (
        ("xidi", "西递古村落"), ("hongcun", "宏村"))),
    34894: ("天一阁", "浙江省宁波市海曙区", (
        ("tianyige", "天一阁博物院"), ("moon-lake", "月湖公园"))),
    34893: ("江郎山", "浙江省衢州市江山市", (
        ("jianglang", "江郎山景区"), ("nianbadu", "廿八都古镇"), ("qingyang", "清漾村"))),
    34887: ("鲁迅", "浙江省绍兴市越城区", (
        ("luxun", "鲁迅故里"), ("shenyuan", "沈园"))),
    34883: ("溪口", "浙江省宁波市奉化区", (
        ("jiang", "蒋氏故居"), ("xuedou", "雪窦山景区"), ("tengtou", "滕头村"))),
    34867: ("沙家浜", "江苏省苏州市常熟市", (
        ("shajiabang", "沙家浜景区"), ("yushan", "虞山景区"), ("shanghu", "尚湖景区"))),
    34864: ("金山", "江苏省镇江市", (
        ("jinshan", "金山景区"), ("jiaoshan", "焦山景区"), ("beigu", "北固山景区"))),
    34854: ("钟山", "江苏省南京市玄武区", (
        ("zhongshan", "中山陵"), ("xiaoling", "明孝陵"), ("linggu", "灵谷寺景区"))),
    34852: ("苏州园林", "江苏省苏州市", (
        ("humble", "拙政园"), ("lingering", "留园"), ("tiger-hill", "虎丘山风景名胜区"))),
    34850: ("纪念馆", "上海市", (
        ("first", "中共一大纪念馆"), ("second", "中共二大会址纪念馆"), ("fourth", "中共四大纪念馆"))),
    34837: ("高句丽", "吉林省通化市集安市", (
        ("guonei", "国内城遗址"), ("wandu", "丸都山城"),
        ("stele", "好太王碑"), ("general-tomb", "将军坟"))),
    34813: ("壶口瀑布", "", (
        ("shanxi", "壶口瀑布（山西）", "山西省临汾市吉县"),
        ("shaanxi", "壶口瀑布（陕西）", "陕西省延安市宜川县"))),
    34802: ("南湖", "河北省唐山市", (
        ("lake", "唐山南湖旅游景区"), ("mine", "开滦国家矿山公园"))),
    34799: ("山海关", "河北省秦皇岛市山海关区", (
        ("pass", "山海关天下第一关"), ("dragon-head", "老龙头景区"))),
    34791: ("避暑山庄", "河北省承德市双桥区", (
        ("resort", "承德避暑山庄"), ("puning", "普宁寺"),
        ("putuo", "普陀宗乘之庙"), ("xumi", "须弥福寿之庙"))),
    34788: ("大运河", "北京市通州区", (
        ("temples", "三庙一塔景区"), ("park", "通州运河公园"), ("forest", "大运河森林公园"))),
    34784: ("十三陵", "北京市昌平区", (
        ("changling", "明长陵"), ("dingling", "明定陵"), ("zhaoling", "明昭陵"))),
    34783: ("长城", "北京市", (
        ("badaling", "八达岭长城", "北京市延庆区"),
        ("mutianyu", "慕田峪长城", "北京市怀柔区"))),
}


def component_id(source_id: int, slug: str) -> str:
    return f"mct-5a-{source_id}-point-{slug}"


# 已核对的遗产游览地。元组依次为稳定 ID、名称、省市地址、归属范围。
# component=遗产组成部分；contains-component=景区含遗产点；viewpoint=沿线观景地。
# 后两者不能被标成“整个景点都是世界遗产”。只选可识别的游览地，不穷举遗产保护子项。
def heritage_place_rules() -> dict[int, tuple[str, list[tuple[str, str, str, str]], list[str]]]:
    def old(number: int, name: str, region: str, scope: str = "component") -> tuple[str, str, str, str]:
        return f"mct-5a-{number}", name, region, scope

    def part(number: int, slug: str, name: str, region: str, scope: str = "component") -> tuple[str, str, str, str]:
        return component_id(number, slug), name, region, scope

    def new(slug: str, name: str, region: str, scope: str = "component") -> tuple[str, str, str, str]:
        return slug, name, region, scope

    return {
        1: ("长城", [
            part(34783, "badaling", "八达岭长城", "北京市延庆区"),
            part(34783, "mutianyu", "慕田峪长城", "北京市怀柔区"),
            old(34801, "金山岭长城", "河北省承德市滦平县"),
            part(34799, "pass", "山海关天下第一关", "河北省秦皇岛市山海关区"),
            part(34799, "dragon-head", "老龙头景区", "河北省秦皇岛市山海关区"),
            part(35103, "fortress", "嘉峪关关城", "甘肃省嘉峪关市"),
            part(35103, "overhanging", "悬壁长城", "甘肃省嘉峪关市"),
            part(35103, "first-beacon", "长城第一墩", "甘肃省嘉峪关市"),
            old(34809, "雁门关景区", "山西省忻州市代县"),
            new("juyongguan", "居庸关长城", "北京市昌平区"),
            new("simatai", "司马台长城", "北京市密云区"),
            new("huanghuacheng", "黄花城水长城", "北京市怀柔区"),
            new("shuiguan", "水关长城", "北京市延庆区"),
        ], ["https://whc.unesco.org/en/list/438/", "https://www.beijing.gov.cn/fuwu/bmfw/sy/jrts/202603/t20260326_4566634.html"]),
        2: ("明清皇宫", [
            old(34780, "故宫博物院（北京）", "北京市东城区"),
            new("shenyang", "沈阳故宫", "辽宁省沈阳市沈河区"),
        ], ["https://whc.unesco.org/en/list/439/"]),
        5: ("泰山", [old(34940, "泰山景区", "山东省泰安市")], ["https://whc.unesco.org/en/list/437/"]),
        9: ("布达拉宫历史建筑群", [
            old(35084, "布达拉宫", "西藏自治区拉萨市"),
            old(35085, "大昭寺", "西藏自治区拉萨市"),
            new("norbulingka", "罗布林卡", "西藏自治区拉萨市"),
        ], ["https://whc.unesco.org/en/list/707/"]),
        10: ("承德避暑山庄及其周围寺庙", [
            part(34791, slug, name, "河北省承德市双桥区")
            for slug, name in FIVE_A_SPLITS[34791][2]
        ], ["https://whc.unesco.org/en/list/703/"]),
        11: ("曲阜孔庙、孔林和孔府", [
            part(34942, slug, name, "山东省济宁市曲阜市")
            for slug, name in FIVE_A_SPLITS[34942][2]
        ], ["https://whc.unesco.org/en/list/704/"]),
        13: ("峨眉山—乐山大佛", [
            old(35047, "峨眉山景区", "四川省乐山市峨眉山市"),
            old(35049, "乐山大佛景区", "四川省乐山市市中区"),
        ], ["https://whc.unesco.org/en/list/779/"]),
        14: ("平遥古城", [
            old(34808, "平遥古城", "山西省晋中市平遥县"),
            new("shuanglin", "双林寺", "山西省晋中市平遥县"),
            new("zhenguo", "镇国寺", "山西省晋中市平遥县"),
        ], ["https://whc.unesco.org/en/list/812/"]),
        15: ("苏州古典园林", [
            part(34852, "humble", "拙政园", "江苏省苏州市"),
            part(34852, "lingering", "留园", "江苏省苏州市"),
            *[new(slug, name, "江苏省苏州市") for slug, name in (
                ("net-master", "网师园"), ("huanxiu", "环秀山庄"), ("canglang", "沧浪亭"),
                ("lion-grove", "狮子林"), ("cultivation", "艺圃"), ("couple", "耦园"), ("retreat", "退思园"))],
        ], ["https://whc.unesco.org/en/list/813/"]),
        16: ("丽江古城", [
            old(35076, "丽江古城（大研古城）", "云南省丽江市古城区"),
            new("shuhe", "束河古镇", "云南省丽江市古城区"),
            new("baisha", "白沙古镇", "云南省丽江市玉龙纳西族自治县"),
        ], ["https://whc.unesco.org/en/list/811/"]),
        19: ("大足石刻", [
            part(35034, "baodingshan", "宝顶山石刻", "重庆市大足区"),
            part(35034, "beishan", "北山石刻", "重庆市大足区"),
        ], ["https://whc.unesco.org/en/list/912/"]),
        20: ("武夷山", [
            old(34914, "武夷山风景名胜区", "福建省南平市武夷山市"),
            new("han-city", "城村汉城遗址", "福建省南平市武夷山市"),
        ], ["https://whc.unesco.org/en/list/911/"]),
        21: ("皖南古村落", [
            part(34903, "xidi", "西递古村落", "安徽省黄山市黟县"),
            part(34903, "hongcun", "宏村", "安徽省黄山市黟县"),
        ], ["https://whc.unesco.org/en/list/1002/"]),
        22: ("明清皇家陵寝", [
            part(34987, "xianling", "明显陵", "湖北省荆门市钟祥市"),
            old(34795, "清东陵", "河北省唐山市遵化市"),
            old(34800, "清西陵", "河北省保定市易县"),
            part(34854, "xiaoling", "明孝陵", "江苏省南京市玄武区"),
            *[part(34784, slug, name, "北京市昌平区") for slug, name in FIVE_A_SPLITS[34784][2]],
            new("yongling", "清永陵", "辽宁省抚顺市新宾满族自治县"),
            new("fuling", "沈阳福陵", "辽宁省沈阳市浑南区"),
            new("zhaoling", "沈阳昭陵", "辽宁省沈阳市皇姑区"),
        ], ["https://whc.unesco.org/en/list/1004/"]),
        24: ("青城山—都江堰", [
            part(35046, slug, name, "四川省成都市都江堰市") for slug, name in FIVE_A_SPLITS[35046][2]
        ], ["https://whc.unesco.org/en/list/1001/"]),
        26: ("高句丽王城、王陵及贵族墓葬", [
            old(34830, "五女山景区", "辽宁省本溪市桓仁满族自治县"),
            *[part(34837, slug, name, "吉林省通化市集安市") for slug, name in FIVE_A_SPLITS[34837][2]],
        ], [detail_url(26, 4)]),
        27: ("澳门历史城区", [
            new(slug, name, "澳门特别行政区") for slug, name in (
                ("a-ma", "妈阁庙"), ("st-paul", "大三巴牌坊"), ("monte", "大炮台"),
                ("guia", "东望洋炮台"), ("mandarin", "郑家大屋"), ("dom-pedro", "岗顶剧院"),
                ("senado", "议事亭前地"), ("st-dominic", "玫瑰堂"))
        ], ["https://www.macaotourism.gov.mo/en/sightseeing/macao-world-heritage"]),
        29: ("开平碉楼与村落", [
            part(35015, "zili", "自力村碉楼群", "广东省江门市开平市"),
            part(35015, "majianglong", "马降龙碉楼群", "广东省江门市开平市"),
            new("jinjiangli", "锦江里碉楼群", "广东省江门市开平市"),
            new("sanmenli", "三门里迎龙楼", "广东省江门市开平市"),
        ], [detail_url(29, 4)]),
        30: ("福建土楼", [
            part(34916, "hongkeng", "洪坑土楼群", "福建省龙岩市永定区"),
            part(34916, "gaobei", "高北土楼群", "福建省龙岩市永定区"),
            part(34916, "tianluokeng", "田螺坑土楼群", "福建省漳州市南靖县"),
            part(34916, "yunshuiyao", "云水谣古镇", "福建省漳州市南靖县", "contains-component"),
            new("chuxi", "初溪土楼群", "福建省龙岩市永定区"),
            new("yanxiang", "衍香楼", "福建省龙岩市永定区"),
            new("zhenfu", "振福楼", "福建省龙岩市永定区"),
            new("hekeng", "河坑土楼群", "福建省漳州市南靖县"),
            new("dadi", "大地土楼群", "福建省漳州市华安县"),
        ], ["https://wlt.fujian.gov.cn/hdjl/wdxd/202305/t20230512_6168233.htm", "https://fgw.fujian.gov.cn/zfxxgkzl/zfxxgkml/yzdgkdqtxx/202009/t20200914_5385798.htm"]),
        31: ("五台山", [
            old(34805, "五台山景区（台怀）", "山西省忻州市五台县"),
            new("foguang", "佛光寺", "山西省忻州市五台县"),
        ], [detail_url(31, 4)]),
        32: ("登封天地之中历史建筑群", [
            old(34956, "嵩山少林景区", "河南省郑州市登封市", "contains-component"),
            *[new(slug, name, "河南省郑州市登封市") for slug, name in (
                ("academy", "嵩阳书院"), ("zhongyue", "中岳庙"), ("observatory", "登封观星台"),
                ("songyue", "嵩岳寺塔"), ("huishan", "会善寺"))],
        ], ["https://whc.unesco.org/en/list/1305/"]),
        35: ("红河哈尼梯田", [
            new(slug, name, "云南省红河哈尼族彝族自治州元阳县") for slug, name in (
                ("duoyishu", "多依树梯田"), ("bada", "坝达梯田"), ("laohuzui", "老虎嘴梯田"))
        ], ["https://www.hhyy.gov.cn/info/1121/195331.htm"]),
        36: ("丝绸之路：长安—天山廊道", [
            part(35092, "pagoda", "大雁塔大慈恩寺", "陕西省西安市", "contains-component"),
            old(35099, "大明宫国家遗址公园", "陕西省西安市"),
            old(35105, "麦积山石窟", "甘肃省天水市"),
            part(35108, "grottoes", "炳灵寺石窟", "甘肃省临夏回族自治州永靖县"),
            *[new(slug, name, region) for slug, name, region in (
                ("weiyang", "汉长安城未央宫遗址", "陕西省西安市"),
                ("xiaoyanta", "小雁塔", "陕西省西安市"),
                ("xingjiao", "兴教寺塔", "陕西省西安市"),
                ("binzhou", "彬州大佛寺石窟", "陕西省咸阳市彬州市"),
                ("zhangqian", "张骞墓", "陕西省汉中市城固县"),
                ("hanwei", "汉魏洛阳故城遗址", "河南省洛阳市"),
                ("dingding", "定鼎门遗址", "河南省洛阳市"),
                ("hangu", "新安汉函谷关遗址", "河南省洛阳市新安县"),
                ("xiaohan", "崤函古道石壕段遗址", "河南省三门峡市陕州区"),
                ("yumen", "玉门关遗址", "甘肃省敦煌市"),
                ("xuanquan", "悬泉置遗址", "甘肃省敦煌市"),
                ("suoyang", "锁阳城遗址", "甘肃省酒泉市瓜州县"),
                ("gaochange", "高昌故城", "新疆维吾尔自治区吐鲁番市"),
                ("jiaohe", "交河故城", "新疆维吾尔自治区吐鲁番市"),
                ("kizilgaha", "克孜尔尕哈烽燧", "新疆维吾尔自治区库车市"),
                ("kizil", "克孜尔石窟", "新疆维吾尔自治区拜城县"),
                ("subash", "苏巴什佛寺遗址", "新疆维吾尔自治区库车市"),
                ("beiting", "北庭故城遗址", "新疆维吾尔自治区吉木萨尔县"))],
        ], [detail_url(36, 4)]),
        37: ("大运河", [
            *[part(34788, slug, name, "北京市通州区", "viewpoint") for slug, name in FIVE_A_SPLITS[34788][2]],
            old(34946, "台儿庄古城", "山东省枣庄市台儿庄区", "contains-component"),
            old(34889, "南浔古镇", "浙江省湖州市南浔区", "contains-component"),
            new("gongchen", "拱宸桥", "浙江省杭州市拱墅区"),
            new("qingming", "清名桥历史文化街区", "江苏省无锡市梁溪区", "viewpoint"),
            new("dongguan", "东关古渡", "江苏省扬州市广陵区", "viewpoint"),
        ], [detail_url(37, 4), "https://www.gongshu.gov.cn/art/2024/4/30/art_1229789753_59080389.html",
            "https://www.wxlx.gov.cn/doc/2016/08/19/1956997.shtml",
            "https://www.beijing.gov.cn/renwen/sy/whkb/202207/t20220729_2781722.html"]),
        38: ("土司遗址", [
            new("laosicheng", "老司城遗址", "湖南省湘西土家族苗族自治州永顺县"),
            new("tangya", "唐崖土司城址", "湖北省恩施土家族苗族自治州咸丰县"),
            new("hailongtun", "海龙屯", "贵州省遵义市汇川区"),
        ], [detail_url(38, 4)]),
        39: ("左江花山岩画文化景观", [
            old(35026, "宁明花山岩画景区", "广西壮族自治区崇左市宁明县")
        ], [detail_url(39, 4)]),
        41: ("良渚古城遗址", [
            new(slug, name, "浙江省杭州市余杭区") for slug, name in (
                ("city", "良渚古城遗址公园"), ("yaoshan", "瑶山遗址公园"), ("laohuling", "老虎岭遗址公园"))
        ], ["https://wgly.hangzhou.gov.cn/cw/cn/index.html"]),
        42: ("泉州：宋元中国的世界海洋商贸中心", [
            part(34918, "mountain", "清源山景区（老君岩）", "福建省泉州市丰泽区", "contains-component"),
            part(34918, "jiuri", "九日山祈风石刻", "福建省泉州市南安市"),
            part(34918, "saints", "伊斯兰教圣墓", "福建省泉州市丰泽区"),
            *[new(slug, name, "福建省泉州市") for slug, name in (
                ("kaiyuan", "开元寺"), ("confucius", "泉州府文庙"), ("qingjing", "清净寺"),
                ("tianhou", "泉州天后宫"), ("deji", "德济门遗址"), ("zhenwu", "真武庙"),
                ("caoyan", "草庵"), ("luoyang", "洛阳桥"), ("anping", "安平桥"),
                ("liusheng", "六胜塔"), ("wanshou", "万寿塔"), ("shihu", "石湖码头"))],
        ], ["https://www.quanzhou.gov.cn/lyb/lyxw/202107/t20210728_2594816.htm", "https://www.quanzhou.gov.cn/zfb/xxgk/zfxxgkzl/qzdt/qzyw/202004/t20200430_2232924.htm"]),
        43: ("普洱景迈山古茶林文化景观", [
            new(slug, name, "云南省普洱市澜沧拉祜族自治县") for slug, name in (
                ("dapingzhang", "大平掌古茶林"), ("wengji", "翁基古寨"),
                ("nuogan", "糯干古寨"), ("dazhai", "景迈大寨"))
        ], ["https://www.forestry.gov.cn/c/www/zrzryc/531328.jhtml", "https://www.ynxc.gov.cn/html/2024/yunnanlunbotu_0209/9761.html"]),
        44: ("北京中轴线", [
            old(34780, "故宫博物院（北京）", "北京市东城区"),
            old(34781, "天坛公园", "北京市东城区"),
            *[new(slug, name, "北京市", scope) for slug, name, scope in (
                ("bell-drum", "北京钟鼓楼", "component"),
                ("jingshan", "景山公园", "component"),
                ("tiananmen", "天安门城楼", "component"),
                ("square", "天安门广场", "component"),
                ("zhengyang", "正阳门", "component"),
                ("yongding", "永定门公园", "contains-component"),
                ("taimiao", "太庙", "component"),
                ("zhongshan", "中山公园（社稷坛）", "contains-component"),
                ("xiannong", "先农坛", "component"))],
        ], ["https://whc.unesco.org/en/list/1714/"]),
    }


def apply_verified_place_names(places: list[dict[str, Any]]) -> None:
    """官方资料已核实的名称与地区；不保存高德 ID 或硬编码坐标。"""
    # 值为：别名、补全后的地区、核实来源。原始 sourceRecords 不作改写。
    rules = {
        "mct-5a-35005-point-eco-park": (["观澜湖生态运动公社"], "", "https://swj.sz.gov.cn/ztzl/swhjs/swllx/content/post_12328291.html"),
        "mct-5a-34921-point-mao-garden": (["古田毛主席纪念园", "主席园"], "福建省龙岩市上杭县古田镇", "https://www.dx.hainan.gov.cn/130/content_15576.html"),
        "mct-5a-35080": (["昆明世界园艺博览园", "世界园艺博览园"], "云南省昆明市盘龙区", detail_url(35080, 10)),
        "mct-5a-35070": (["镇远古城"], "贵州省黔东南苗族侗族自治州镇远县", detail_url(35070, 10)),
        "mct-5a-35066": (["中国百里杜鹃风景名胜区"], "贵州省毕节市黔西市", "https://mzt.guizhou.gov.cn/xxgk/xxgkml/zdlyxx1/qhdm_88523/list_2.html"),
        "mct-5a-34968": (["芒砀山旅游区"], "河南省商丘市永城市", "https://zhuanti.mct.gov.cn/rxhmxjgn2022/henan/detail/2863.html"),
        "mct-5a-34936": (["萍乡武功山国家级风景名胜区"], "江西省萍乡市芦溪县", detail_url(34936, 10)),
        "mct-5a-34922": (["湄洲岛国家旅游度假区"], "福建省莆田市秀屿区", "https://mzd.putian.gov.cn/xxgk/xwzx/gzdt/202504/t20250410_1989777.htm"),
        "mct-5a-34911": (["采石矶"], "安徽省马鞍山市雨山区", detail_url(34911, 10)),
        "mct-5a-34846": (["扎龙国家级自然保护区"], "黑龙江省齐齐哈尔市铁锋区", detail_url(34846, 10)),
        "mct-5a-34865": (["太湖鼋头渚风景区"], "江苏省无锡市滨湖区", "https://bigdata.wuxi.gov.cn/doc/2017/08/22/4258676.shtml"),
        "mct-5a-34838": (["查干湖景区"], "吉林省松原市前郭尔罗斯蒙古族自治县", detail_url(34838, 10)),
        "mct-5a-34829": (["红海滩国家风景廊道"], "辽宁省盘锦市大洼区", detail_url(34829, 10)),
        "mct-5a-34825": (["大连老虎滩海洋公园"], "辽宁省大连市中山区", detail_url(34825, 10)),
        "mct-5a-34850-point-first": (["中国共产党第一次全国代表大会纪念馆"], "上海市黄浦区", "https://www.shanghai.gov.cn/hsly/20260625/10d0e931417d4614b0d8bbbbd03495c1.html"),
        "mct-world-heritage-6": (["周口店国家考古遗址公园"], "北京市房山区", "https://www.beijing.gov.cn/renwen/rwzyd/qxdw/dmfshqxty/zhkdbjryzh/202309/t20230922_3264885.html"),
        "mct-5a-35109-point-eman": (["官鹅沟大景区鹅嫚沟"], "", detail_url(35109, 10)),
        "mct-5a-35071-point-waterfall": (["赤水丹霞旅游区大瀑布"], "", detail_url(35071, 10)),
        "mct-5a-35071-point-yanziyan": (["赤水丹霞旅游区燕子岩"], "", detail_url(35071, 10)),
        "mct-5a-35081-point-volcano": (["腾冲火山群"], "云南省保山市腾冲市马站乡", "https://www.tengchong.gov.cn/info/8830/3093713.htm"),
        "mct-5a-35038-point-stone-sea": (["万盛石林风景区"], "重庆市綦江区", "https://ws.cq.gov.cn/zwgk_165/fdzdgknr/jytablqk/zxta/202003/t20200301_5537609.html"),
        "mct-5a-34948-point-guimeng": (["沂蒙山旅游区龟蒙景区"], "", detail_url(34948, 10)),
        "mct-5a-34948-point-yunmeng": (["沂蒙山云蒙景区"], "", detail_url(34948, 10)),
        "mct-5a-34918-point-saints": (["灵山伊斯兰教圣墓"], "", "https://www.quanzhou.gov.cn/lyb/lswh/zdww/qgzdwwbhdwjj/202311/t20231130_2974355.htm"),
        "mct-5a-34907-point-qiankou": (["潜口民宅博物馆"], "安徽省黄山市徽州区", "https://www.huangshan.gov.cn/site/tpl/4636?id=19776&type=scenic"),
        "mct-5a-34884-point-qin-palace": (["横店影视城秦王宫园区"], "", detail_url(34884, 10)),
        "mct-5a-34884-point-riverside": (["横店影视城清明上河图园区"], "", detail_url(34884, 10)),
        "mct-5a-34884-point-imperial-palace": (["横店影视城明清宫苑园区"], "", detail_url(34884, 10)),
        "mct-5a-34884-point-dream-valley": (["横店影视城梦幻谷园区"], "", detail_url(34884, 10)),
        "mct-5a-34870-point-hexia": (["千年古镇河下"], "", "https://gaj.huaian.gov.cn/col/9073_614378/art/17698752/1771039462382cD9wL65S.html"),
        "mct-5a-35034-point-baodingshan": (["大足石刻宝顶山景区", "宝顶山"], "", "https://www.dazu.gov.cn/qzfbm/qyjj/zwgk_53321/fdzdgknr_53323/yjgl/yjya/202312/t20231208_12674916.html"),
        "mct-5a-35034-point-beishan": (["大足石刻名胜风景区北山石刻"], "", detail_url(35034, 10)),
        "mct-5a-35015-point-majianglong": (["开平碉楼文化旅游区(马降龙景区)"], "", "https://www.kaiping.gov.cn/kpszfw/zwgk/zdlyxxgkzl/lyscjgzfxx/lyml/content/post_2867978.html"),
        "mct-5a-34999-point-shibadong": (["十八洞景区"], "湖南省湘西土家族苗族自治州花垣县", detail_url(34999, 10)),
        "mct-5a-34999-point-dehang": (["德夯峡谷景区"], "湖南省湘西土家族苗族自治州吉首市", "https://www.hengyang.gov.cn/xxgk/dtxx/tzgg/gsgg/20220929/i2802912.html"),
        "mct-5a-34989-point-wulingyuan": (["张家界国家森林公园"], "湖南省张家界市武陵源区", detail_url(34989, 10)),
        "mct-5a-34942-point-mansion": (["曲阜三孔景区-孔府"], "", detail_url(34942, 10)),
        "mct-5a-34916-point-gaobei": (["福建土楼永定景区·福建土楼王景区"], "", "https://www.yongding.gov.cn/xzjd/gtxr/xzjs_44750/"),
        "mct-5a-34893-point-qingyang": (["清漾毛氏文化村"], "", "https://www.zjsjw.gov.cn/zhuantizhuanlan/qinglianwenhua/qingfengzhilv/201512/t20151207_2637818.shtml"),
        "mct-5a-34799-point-pass": (["山海关景区·天下第一关"], "", detail_url(34799, 10)),
        "mct-5a-34813-point-shanxi": (["山西黄河壶口瀑布旅游区"], "", detail_url(34813, 10)),
        "mct-5a-34784-point-changling": (["明十三陵-长陵"], "", detail_url(34784, 10)),
        "mct-5a-34784-point-dingling": (["明十三陵-定陵"], "", detail_url(34784, 10)),
        "mct-5a-34784-point-zhaoling": (["明十三陵-昭陵"], "", detail_url(34784, 10)),
        "mct-world-heritage-1-point-shuiguan": (["八达岭水关长城"], "", "https://www.beijing.gov.cn/renwen/rwzyd/lyjq/4A/bdlsgccjq/202210/t20221020_2839768.html"),
        "mct-world-heritage-22-point-fuling": (["清福陵"], "", "https://whly.ln.gov.cn/whly/wlzt/lnww/sjwhycd/D1EF78550DD043D783C4A10D0343109B/index.shtml"),
        "mct-world-heritage-27-point-st-dominic": (["玫瑰圣母堂"], "", "https://www.macaotourism.gov.mo/zh-hans/sightseeing/churches"),
        "mct-world-heritage-29-point-sanmenli": (["迎龙楼"], "", "https://www.kaiping.gov.cn/ckzrmzf/ckly/content/post_3485709.html"),
        "mct-world-heritage-35-point-duoyishu": (["多依树观景区"], "", "https://www.hh.gov.cn/ldxx_xx.jsp?leadermailid=4B937DAFC5CAC10F6AD2C0BEF1A0E178&urltype=leadermail.LeaderMailContentUrl&wbtreeid=1481"),
        "mct-world-heritage-35-point-bada": (["坝达景区"], "", "https://www.hh.gov.cn/info/10521/852962.htm"),
        "mct-world-heritage-42-point-deji": (["天后宫-德济门遗址"], "福建省泉州市鲤城区", "https://www.quanzhou.gov.cn/lyb/lswh/zdww/201510/t20151029_183287.htm"),
        "mct-world-heritage-42-point-zhenwu": ([], "福建省泉州市丰泽区", "https://www.qzfz.gov.cn/zjfz/whly/202403/t20240305_3010989.htm"),
        "mct-world-heritage-42-point-confucius": (["文庙"], "福建省泉州市鲤城区", "https://www.quanzhou.gov.cn/lyb/lswh/zdww/201609/t20160913_372291.htm"),
        "mct-world-heritage-42-point-caoyan": (["草庵摩尼教遗址公园"], "福建省泉州市晋江市", "https://www.fjtb.gov.cn/focus/2017fjhxlw/201708/t20170802_11826446.html"),
        "mct-world-heritage-42-point-wanshou": (["姑嫂塔"], "福建省泉州市石狮市", "https://www.shishi.gov.cn/zjss/csfm/201709/t20170930_545547.htm"),
        "mct-world-heritage-42-point-shihu": (["林銮渡"], "福建省泉州市石狮市", "https://www.shishi.gov.cn/zjss/csfm/201709/t20170929_544500.htm"),
        "mct-world-heritage-36-point-binzhou": (["大佛寺"], "陕西省咸阳市彬州市", "https://binzhou.qinfeng.gov.cn/info/1014/3758.htm"),
    }
    for place in places:
        rule = rules.get(place["id"])
        if rule is None:
            continue
        aliases, region, evidence = rule
        place["aliases"] = list(dict.fromkeys([*place.get("aliases", []), *aliases]))
        if region:
            place["regionText"] = region
        if evidence not in place.setdefault("selectionEvidence", []):
            place["selectionEvidence"].append(evidence)
        note = {"aliases": aliases, "regionText": region, "url": evidence}
        if note not in place.setdefault("nameEvidence", []):
            place["nameEvidence"].append(note)

    # 代表点不是景点别名：明确保存两者关系，不把入口或所属场所当成景点本体。
    representatives = {
        "mct-5a-35109-point-guanzhu": (
            "官鹅沟大景区官珠沟游客服务中心", "visitor-center", "游客中心", "",
            "https://zwfw.gansu.gov.cn/tanchang/tsfw/szsc/ywjj/art/2025/art_0da0877e095040318562cf9f1f2fb951.html"),
        "mct-5a-35108-point-stone-forest": (
            "大寺沟石柱(姊妹峰)", "landmark", "姊妹峰", "",
            "https://www.gsyongjing.gov.cn/yjx/zmyj/sjyj/JQJD/art/2022/art_ad683aeb6139493c96c1e2cbf581a8ff.html"),
        "mct-5a-34837-point-guonei": (
            "国内城东门遗址", "historic-gate", "东门遗址", "",
            "https://www.amap.com/place/B01B30MGL3"),
        "mct-world-heritage-36-point-xingjiao": (
            "护国兴教寺", "venue", "所属寺院：护国兴教寺", "陕西省西安市长安区",
            "https://dfz.shaanxi.gov.cn/zslm/fzzlk/xbsxsxz/xbsxz/xas_16198/201711/P020240923626792713048.pdf"),
        "mct-world-heritage-43-point-dazhai": (
            "景迈大寨", "village", "村庄代表点", "",
            "https://policy.mofcom.gov.cn/claw/clawContent.shtml?id=61942"),
    }
    for place in places:
        rule = representatives.get(place["id"])
        if rule is None:
            continue
        name, kind, label, region, evidence = rule
        place["representativePoint"] = {"name": name, "kind": kind, "label": label, "evidence": [evidence]}
        if region:
            place["regionText"] = region
        place.setdefault("selectionEvidence", []).append(evidence)


def resolve_independent_places(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_id = {record["id"]: record for record in records}
    removed: set[str] = set()
    groups: list[dict[str, Any]] = []

    def compact_name(value: str) -> str:
        return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", value)

    def make_place(item_id: str, name: str, region: str) -> dict[str, Any]:
        hints = province_hints(region)
        if len(hints) != 1:
            raise DataContractError(f"独立景点省份不唯一：{name}")
        return {
            "id": item_id, "name": name, "classifications": [], "regionText": region,
            "provinceHints": hints, "description": "", "officialImageUrl": "",
            "geocodeQuery": f"{region}{name}", "reviewStatus": "ready", "reviewReason": "",
            "sources": [], "sourceRecordIds": [], "selectionEvidence": [],
        }

    def attach(place: dict[str, Any], parent: dict[str, Any], scope: str, label: str, evidence: list[str]) -> None:
        for classification in parent["classifications"]:
            value = {**classification, "scope": scope, "groupName": label}
            if value not in place["classifications"]:
                place["classifications"].append(value)
        for field in ("sources", "sourceRecordIds"):
            for value in parent[field]:
                if value not in place[field]:
                    place[field].append(value)
        for url in evidence:
            if url not in place.setdefault("selectionEvidence", []):
                place["selectionEvidence"].append(url)
        if not place.get("description"):
            place["description"] = f"{place['name']}，按独立游览地点整理；所属官方项目：{label}。完整官方介绍见 sourceRecords。"
            place["descriptionSource"] = "整理说明，非单独采集的景点介绍"

    for number, (expected_token, region, definitions) in FIVE_A_SPLITS.items():
        source_id = f"mct-5a-{number}"
        parent = by_id.get(source_id)
        if parent is None:
            continue
        if expected_token not in parent["name"]:
            raise DataContractError(f"{source_id} 与已核对的拆分名称不一致")
        point_ids: list[str] = []
        for definition in definitions:
            slug, name = definition[:2]
            point_region = definition[2] if len(definition) == 3 else region
            point_id = component_id(number, slug)
            place = make_place(point_id, name, point_region)
            attach(place, parent, "component", parent["name"], [source["url"] for source in parent["sources"]])
            by_id[point_id] = place
            point_ids.append(point_id)
        removed.add(source_id)
        groups.append({"sourceRecordId": source_id, "name": parent["name"], "placeIds": point_ids})

    for number, (label, definitions, evidence) in heritage_place_rules().items():
        source_id = f"mct-world-heritage-{number}"
        parent = by_id.get(source_id)
        if parent is None:
            continue
        expected_name = "长安天山廊道" if number == 36 else label
        if compact_name(expected_name) not in compact_name(parent["name"]):
            raise DataContractError(f"{source_id} 与已核对的遗产名称不一致")
        point_ids = []
        for key, name, region, scope in definitions:
            point_id = key if key.startswith("mct-") else f"{source_id}-point-{key}"
            if key.startswith("mct-") and (key not in by_id or key in removed):
                raise DataContractError(f"{source_id} 关联的独立景点不存在：{key}")
            place = by_id.get(point_id)
            if place is None:
                place = make_place(point_id, name, region)
                by_id[point_id] = place
            else:
                hints = province_hints(region)
                if len(hints) != 1 or not set(hints) & set(place["provinceHints"]):
                    raise DataContractError(f"独立景点地区与原始资料不符：{point_id}")
                place["aliases"] = list(dict.fromkeys([*place.get("aliases", []), place["name"], name]))
                place.update(name=name, regionText=region, provinceHints=hints,
                             geocodeQuery=f"{region}{name}", reviewStatus="ready", reviewReason="")
                place.pop("possibleDuplicateIds", None)
                place.pop("possibleComponentIds", None)
            attach(place, parent, scope, label, evidence)
            point_ids.append(point_id)
        removed.add(source_id)
        groups.append({"sourceRecordId": source_id, "name": parent["name"], "placeIds": point_ids,
                       "selection": "独立游览地；非遗产保护子项全量清单", "evidence": evidence})

    places = [place for key, place in by_id.items() if key not in removed]
    apply_verified_place_names(places)
    queries: set[str] = set()
    for place in places:
        # 完整原记录统一保存在文档顶层，不给每个子景点复制整段集合介绍和图片。
        place.pop("sourceRecords", None)
        if place["reviewStatus"] == "ready":
            query = normalize_name(place["geocodeQuery"])
            if not query or query in queries:
                raise DataContractError(f"独立景点查询为空或重复：{place['name']}")
            queries.add(query)
    source_ids = {source_id for place in places for source_id in place["sourceRecordIds"]}
    expected_ids = {source_id for record in records for source_id in record["sourceRecordIds"]}
    if not expected_ids <= source_ids:
        raise DataContractError("独立景点整理丢失了官方来源关联")
    return places, groups


def _fetch_detail(
    source_id: int,
    category_id: int,
    *,
    open_url: OpenUrl,
) -> dict[str, Any]:
    url = detail_url(source_id, category_id)
    return parse_detail_page(
        fetch_text(url, open_url=open_url),
        category_id=category_id,
        detail_url=url,
    )


def run_pipeline(
    *,
    output_path: Path,
    report_path: Path,
    open_url: OpenUrl = urlopen,
    detail_workers: int = 4,
    timestamp: Timestamp = iso_timestamp,
) -> int:
    output_path = Path(output_path)
    report_path = Path(report_path)
    if output_path.resolve() == report_path.resolve():
        raise DataContractError("第一阶段输出与报告不能使用同一路径")
    if detail_workers < 1:
        raise DataContractError("详情并发数必须至少为 1")

    generated_at = timestamp()
    report: dict[str, Any] = {
        "generatedAt": generated_at,
        "status": "failed",
        "output": str(output_path),
        "sourceCounts": {"fiveA": 0, "worldHeritage": 0},
        "worldHeritageListRows": 0,
        "crossClassificationReviewPairs": [],
        "needsReviewItems": [],
        "sourceWarningItems": [],
        "sourceWarningCount": 0,
        "mergedCount": 0,
        "totalItems": 0,
        "readyCount": 0,
        "needsReviewCount": 0,
        "error": "",
    }

    try:
        five_a_list, five_a_total = fetch_spot_list(10, open_url=open_url)
        five_a_ids: list[int] = []
        for item in five_a_list:
            try:
                five_a_ids.append(int(item["id"]))
            except (KeyError, TypeError, ValueError) as error:
                raise DataContractError("5A 列表包含无效 ID") from error
        if len(set(five_a_ids)) != len(five_a_ids):
            raise DataContractError("5A 列表包含重复 ID")

        heritage_index = fetch_text(HERITAGE_INDEX_URL, open_url=open_url)
        heritage_count = parse_heritage_count(heritage_index)
        heritage_list, heritage_list_total = fetch_spot_list(4, open_url=open_url)
        heritage_list_names = unique_official_names(
            heritage_list,
            label="世界文化遗产",
        )
        if len(heritage_list_names) != heritage_count:
            raise DataContractError(
                "世界文化遗产列表去重名称数"
                f" {len(heritage_list_names)} 与页面报告总数 {heritage_count} 不一致"
            )
        heritage_ids = list(range(1, heritage_count + 1))

        with ThreadPoolExecutor(max_workers=detail_workers) as executor:
            five_a_details = list(
                executor.map(
                    lambda source_id: _fetch_detail(source_id, 10, open_url=open_url),
                    five_a_ids,
                )
            )
            heritage_details = list(
                executor.map(
                    lambda source_id: _fetch_detail(source_id, 4, open_url=open_url),
                    heritage_ids,
                )
            )

        if len(five_a_details) != five_a_total:
            raise DataContractError("5A 详情数与官方报告总数不一致")
        if len(heritage_details) != heritage_count:
            raise DataContractError("世界文化遗产详情数与官方报告总数不一致")
        heritage_detail_names = {
            normalize_name(detail["name"]) for detail in heritage_details
        }
        if heritage_detail_names != heritage_list_names:
            raise DataContractError("世界文化遗产详情名称与官方列表名称不一致")

        five_a_records = [
            build_five_a_record(item, detail)
            for item, detail in zip(five_a_list, five_a_details, strict=True)
        ]
        heritage_records = [
            build_heritage_record(source_id, detail)
            for source_id, detail in zip(heritage_ids, heritage_details, strict=True)
        ]
        source_records = deepcopy([*five_a_records, *heritage_records])
        working_records = deepcopy(source_records)
        for record in working_records:
            record["sourceRecordIds"] = [record["id"]]
            for classification in record["classifications"]:
                classification["sourceRecordId"] = record["id"]
                classification["sourceName"] = record["name"]
        items, merged_count = merge_exact_records(working_records)
        items, confirmed_merge_count = merge_confirmed_records(items)
        merged_count += confirmed_merge_count
        cross_classification_pairs = mark_cross_classification_relationships(items)
        items, resolved_groups = resolve_independent_places(items)
        remaining_ids = {item["id"] for item in items}
        cross_classification_pairs = [
            pair for pair in cross_classification_pairs
            if pair["fiveAId"] in remaining_ids and pair["worldHeritageId"] in remaining_ids
        ]
        ready_count = sum(item["reviewStatus"] == "ready" for item in items)
        needs_review_count = sum(item["reviewStatus"] == "needs_review" for item in items)
        needs_review_items: list[dict[str, Any]] = []
        source_warning_items: list[dict[str, Any]] = []
        for item in source_records:
            if item.get("sourceDiscrepancies"):
                source_warning_items.append(
                    {
                        "id": item["id"],
                        "name": item["name"],
                        "discrepancies": item["sourceDiscrepancies"],
                    }
                )
        for item in items:
            if item["reviewStatus"] != "needs_review":
                continue
            review_item: dict[str, Any] = {
                "id": item["id"],
                "name": item["name"],
                "reason": item.get("reviewReason", "需人工审核"),
            }
            for field in ("possibleDuplicateIds", "possibleComponentIds"):
                if item.get(field):
                    review_item[field] = item[field]
            needs_review_items.append(review_item)

        document = {
            "schemaVersion": 1,
            "stage": "mct-source",
            "generatedAt": generated_at,
            "placeSelection": "一条记录对应一个独立游览地；大型遗产选取主要游览地点，不穷举遗产保护子项",
            "sources": [
                {
                    "name": "文化和旅游部｜国家5A级旅游景区",
                    "url": f"{MCT_ORIGIN}/site/dataservice/rural?type=10",
                    "itemCount": five_a_total,
                },
                {
                    "name": "文化和旅游部｜世界文化遗产",
                    "url": HERITAGE_INDEX_URL,
                    "itemCount": heritage_count,
                },
            ],
            "summary": {
                "total": len(items),
                "ready": ready_count,
                "needsReview": needs_review_count,
                "sourceFiveA": five_a_total,
                "sourceWorldHeritage": heritage_count,
                "merged": merged_count,
                "confirmedMerged": confirmed_merge_count,
                "resolvedGroups": len(resolved_groups),
                "crossClassificationReviewPairs": len(cross_classification_pairs),
                "sourceWarnings": len(source_warning_items),
            },
            "sourceRecords": source_records,
            "items": items,
        }
        atomic_write_json(output_path, document)

        report.update(
            {
                "status": "success",
                "sourceCounts": {"fiveA": five_a_total, "worldHeritage": heritage_count},
                "worldHeritageListRows": heritage_list_total,
                "crossClassificationReviewPairs": cross_classification_pairs,
                "needsReviewItems": needs_review_items,
                "sourceWarningItems": source_warning_items,
                "sourceWarningCount": len(source_warning_items),
                "worldHeritageDetailIds": {
                    "start": heritage_ids[0],
                    "end": heritage_ids[-1],
                    "expected": heritage_count,
                    "fetched": len(heritage_details),
                    "strategy": "detail-ids-1-through-page-count",
                    "crossCheckedAgainstUniqueListNames": True,
                },
                "mergedCount": merged_count,
                "confirmedMergedCount": confirmed_merge_count,
                "resolvedGroups": resolved_groups,
                "totalItems": len(items),
                "readyCount": ready_count,
                "needsReviewCount": needs_review_count,
                "error": "",
            }
        )
        atomic_write_json(report_path, report)
        return 0
    except (DataContractError, OSError) as error:
        report["error"] = str(error) if isinstance(error, DataContractError) else "本地文件读写失败"
        atomic_write_json(report_path, report)
        return 1


def reorganize_saved_source(output_path: Path, report_path: Path) -> int:
    """仅重新整理已经采集的官方原记录，不重新访问文旅部。"""
    if output_path.resolve() == report_path.resolve():
        raise DataContractError("基础资料和报告不能使用相同路径")
    try:
        document = json.loads(output_path.read_text(encoding="utf-8"))
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise DataContractError("无法读取已有基础资料和采集报告，请先运行完整采集") from error
    if not isinstance(document, dict) or document.get("stage") != "mct-source" or not document.get("sourceRecords") or not isinstance(report, dict):
        raise DataContractError("已有文件缺少官方原始记录")
    working = deepcopy(document["sourceRecords"])
    for record in working:
        record["sourceRecordIds"] = [record["id"]]
        for classification in record["classifications"]:
            classification.update(sourceRecordId=record["id"], sourceName=record["name"])
    items, merged = merge_exact_records(working)
    items, confirmed = merge_confirmed_records(items)
    pairs = mark_cross_classification_relationships(items)
    items, groups = resolve_independent_places(items)
    remaining = {item["id"] for item in items}
    pairs = [pair for pair in pairs if pair["fiveAId"] in remaining and pair["worldHeritageId"] in remaining]
    ready = sum(item["reviewStatus"] == "ready" for item in items)
    review = [{"id": item["id"], "name": item["name"], "reason": item.get("reviewReason", "需人工审核")}
              for item in items if item["reviewStatus"] == "needs_review"]
    document.update(items=items, generatedAt=iso_timestamp())
    document["summary"].update(total=len(items), ready=ready, needsReview=len(review),
                               merged=merged + confirmed, confirmedMerged=confirmed,
                               resolvedGroups=len(groups), crossClassificationReviewPairs=len(pairs))
    report.update(totalItems=len(items), readyCount=ready, needsReviewCount=len(review),
                  needsReviewItems=review, mergedCount=merged + confirmed, confirmedMergedCount=confirmed,
                  resolvedGroups=groups, crossClassificationReviewPairs=pairs)
    atomic_write_json(output_path, document)
    atomic_write_json(report_path, report)
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="获取文旅部 5A 景区与世界文化遗产数据")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH, help="第一阶段 JSON")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH, help="运行报告")
    parser.add_argument("--reuse-source", action="store_true", help="只用已保存的 sourceRecords 重新整理名称、地区和独立景点，不联网")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        operation = reorganize_saved_source if args.reuse_source else run_pipeline
        result = operation(output_path=args.output, report_path=args.report)
    except DataContractError as error:
        print(f"文旅部数据阶段无法启动：{error}", file=sys.stderr)
        return 2
    if result:
        print(f"文旅部数据获取失败，详情见 {args.report}", file=sys.stderr)
    else:
        print(f"文旅部数据已生成：{args.output}")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
