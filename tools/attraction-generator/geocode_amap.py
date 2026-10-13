#!/usr/bin/env python3
"""保存高德的完整请求和响应；原始数据文件本身就是断点记录。"""

from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
from http.client import IncompleteRead
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import unicodedata
import uuid
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote, quote_plus, urlencode
from urllib.request import Request, urlopen

from fetch_mct import PROVINCE_ALIASES


TOOL_ROOT = Path(__file__).resolve().parent
DEFAULT_INPUT_PATH = TOOL_ROOT / "tmp/01-文旅部景点数据.json"
DEFAULT_OUTPUT_PATH = TOOL_ROOT / "tmp/02-高德原始数据.json"
LOCK_PATH = TOOL_ROOT / "tmp/.高德采集.lock"
AMAP_ENDPOINT = "https://restapi.amap.com/v3/geocode/geo"
POI_ENDPOINT = "https://restapi.amap.com/v3/place/text"
REQUEST_HEADERS = {"User-Agent": "roam-attraction-collector/1.0"}
MIN_REQUEST_INTERVAL_SECONDS = 0.5
RETRY_BACKOFF_SECONDS = (1.0, 2.0, 4.0)
REQUEST_TIMEOUT_SECONDS = 30.0
REUSABLE_OUTCOMES = {"success", "no_result", "permanent_error"}

# Key、权限、IP 封停和请求协议问题影响整批查询，立即停止。
FATAL_CODES = {
    "10001", "10002", "10005", "10006", "10007", "10008", "10009",
    "10010", "10011", "10012", "10013", "10024", "10025", "10026",
    "10030", "10031", "10041", "10042", "10043",
    "20001", "20002", "40000", "40002", "40003",
}
PERMANENT_QUERY_CODES = {"20000", "20011", "20012"}


class DataError(RuntimeError):
    """可直接显示的输入或运行错误。"""


def iso_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def normalize_query(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def query_parameters(query: str) -> dict[str, str]:
    return {"address": normalize_query(query), "output": "JSON"}


def query_fingerprint(query: str) -> str:
    return request_fingerprint(AMAP_ENDPOINT, query_parameters(query))


def request_fingerprint(endpoint: str, params: dict[str, str]) -> str:
    # 地理编码和 POI 搜索分别缓存；提取、匹配规则变化不重新发送同一请求。
    request = {"method": "GET", "endpoint": endpoint, "params": params}
    material = json.dumps(request, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            # 完整 POI 正文较大，紧凑编码减少每次原子保存的磁盘写入和断点开销。
            output.write(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise DataError(f"文件不存在：{path}") from None
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise DataError(f"JSON 文件无法解析：{path}") from None


def read_source(path: Path) -> dict[str, Any]:
    document = read_json(path)
    if (
        not isinstance(document, dict)
        or document.get("schemaVersion") != 1
        or document.get("stage") not in {"mct-source", "attraction-source"}
        or not isinstance(document.get("items"), list)
    ):
        raise DataError("景点基础资料格式无效，应为 schemaVersion=1 的文旅部或统一景点资料")
    seen: set[str] = set()
    for item in document["items"]:
        if not isinstance(item, dict):
            raise DataError("基础资料 items 中存在非对象记录")
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id.strip() or item_id in seen:
            raise DataError("基础资料中的 id 必须非空且唯一")
        seen.add(item_id)
        if not isinstance(item.get("name"), str) or not item["name"].strip():
            raise DataError(f"{item_id} 缺少景点名称")
        status = item.get("reviewStatus")
        if status not in {"ready", "needs_review", "skip"}:
            raise DataError(f"{item_id} 的 reviewStatus 无效")
        if status == "ready":
            query = item.get("geocodeQuery")
            if not isinstance(query, str) or not normalize_query(query):
                raise DataError(f"{item_id} 缺少 geocodeQuery")
        if status == "skip":
            reason = item.get("skipReason")
            if not isinstance(reason, str) or not reason.strip():
                raise DataError(f"{item_id} 跳过时需填写 skipReason")
        point = item.get("representativePoint")
        if point is not None and (
            not isinstance(point, dict)
            or point.get("kind") not in {"visitor-center", "landmark", "historic-gate", "venue", "village", "boarding-point"}
            or any(not isinstance(point.get(key), str) or not point[key].strip() for key in ("name", "label"))
            or not isinstance(point.get("evidence"), list) or not point["evidence"]
            or any(not isinstance(url, str) or not url.startswith("https://") for url in point["evidence"])
        ):
            raise DataError(f"{item_id} 的代表点缺少明确名称、用途或核实依据")
        venue = item.get("verifiedVenue")
        if venue is not None and (
            not isinstance(venue, dict)
            or any(not isinstance(venue.get(key), list) or not venue[key]
                   or any(not isinstance(value, str) or not value.strip() for value in venue[key])
                   for key in ("names", "addresses", "evidence"))
            or any(not url.startswith("https://") for url in venue["evidence"])
            or point is not None
            or not any(c.get("type") == "a-level" for c in item.get("classifications", []))
        ):
            raise DataError(f"{item_id} 的景区整体地点缺少 A 级身份、明确名称、地址或核实依据")
    return document


def new_archive() -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "stage": "amap-raw",
        "provider": "amap",
        "createdAt": iso_timestamp(),
        "updatedAt": iso_timestamp(),
        "items": [],
        "requests": [],
    }


def read_archive(path: Path) -> dict[str, Any]:
    archive = read_json(path)
    if (
        not isinstance(archive, dict)
        or archive.get("schemaVersion") != 1
        or archive.get("stage") != "amap-raw"
        or archive.get("provider") != "amap"
        or not isinstance(archive.get("items"), list)
        or not isinstance(archive.get("requests"), list)
    ):
        raise DataError("高德原始数据格式无效")
    seen_request_ids: set[str] = set()
    for record in archive["requests"]:
        if not isinstance(record, dict):
            raise DataError("高德请求记录格式无效")
        request_id = record.get("requestId")
        if not isinstance(request_id, str) or not request_id or request_id in seen_request_ids:
            raise DataError("高德 requestId 必须非空且唯一")
        seen_request_ids.add(request_id)
        request = record.get("request")
        if not isinstance(request, dict) or not isinstance(request.get("params"), dict):
            raise DataError(f"请求 {request_id} 缺少参数")
        params = {key: value for key, value in request["params"].items() if key != "key"}
        endpoint = request.get("endpoint")
        if (
            request.get("method") != "GET"
            or endpoint not in {AMAP_ENDPOINT, POI_ENDPOINT}
            or request["params"].get("key") != "[REDACTED]"
            or not all(isinstance(value, str) for value in params.values())
            or not params.get("address" if endpoint == AMAP_ENDPOINT else "keywords", "").strip()
            or record.get("queryFingerprint") != request_fingerprint(endpoint, params)
        ):
            raise DataError(f"请求 {request_id} 的接口、参数或指纹不一致")
        if record.get("response") is not None and not isinstance(record["response"], dict):
            raise DataError(f"请求 {request_id} 的响应格式无效")
    return archive


def classify_response(record: dict[str, Any]) -> tuple[str, str]:
    response = record.get("response")
    if response is None:
        if record.get("outcome") == "pending":
            return "pending", "上次请求未完成"
        return "retryable_error", "未收到完整响应"
    status_code = response.get("httpStatus")
    if status_code in {401, 403}:
        return "fatal_error", f"高德鉴权或权限失败（HTTP {status_code}）"
    if response.get("complete") is not True:
        return "retryable_error", "响应正文未接收完整"
    if not isinstance(status_code, int) or not 200 <= status_code < 300:
        return "retryable_error", f"高德 HTTP 状态异常：{status_code}"
    body = response.get("body")
    if not isinstance(body, dict):
        return "retryable_error", "高德响应不是有效 JSON 对象"
    if str(body.get("status", "")) != "1":
        code = str(body.get("infocode", ""))
        safe_code = code if re.fullmatch(r"\d{5}", code) else "unknown"
        if code in {"10003", "10044", "10045"}:
            return "fatal_error", f"高德当日调用额度已用尽：{safe_code}"
        if code in FATAL_CODES:
            return "fatal_error", f"高德权限或全局请求错误：{safe_code}"
        if code in PERMANENT_QUERY_CODES:
            return "permanent_error", f"高德拒绝该查询：{safe_code}"
        return "retryable_error", f"高德限流、服务或未知错误：{safe_code}"
    is_poi = record["request"]["endpoint"] == POI_ENDPOINT
    results = body.get("pois" if is_poi else "geocodes")
    count = body.get("count")
    if (
        not isinstance(results, list)
        or not re.fullmatch(r"\d+", str(count))
        or (not is_poi and int(str(count)) != len(results))
    ):
        return "retryable_error", "高德结果数与返回数组不一致"
    if not results:
        return "no_result", "高德未找到地点"
    return "success", ""


def reusable_requests(archive: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """直接从完整响应恢复断点，不依赖另一个可能尚未写入的坐标缓存。"""
    result: dict[str, dict[str, Any]] = {}
    for record in archive["requests"]:
        outcome, _ = classify_response(record)
        if outcome in REUSABLE_OUTCOMES:
            result[record["queryFingerprint"]] = record
    return result


def merge_reusable_requests(available: dict[str, dict[str, Any]], archive: dict[str, Any]) -> None:
    """复用请求时保留所有景点关联，响应正文和实际请求参数不变。"""
    for fingerprint, record in reusable_requests(archive).items():
        previous = available.get(fingerprint, {})
        ids = sorted(set(previous.get("attractionIds", [])) | set(record.get("attractionIds", [])))
        available[fingerprint] = {**record, "attractionIds": ids}


@contextmanager
def collector_lock():
    # 系统锁会在正常退出、Ctrl-C、强制终止或断电后自动释放。
    # 固定锁路径也能阻止使用不同 --output 同时运行本工具。
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise DataError("已有高德采集进程正在运行") from None
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def redact(value: str, api_key: str) -> str:
    for secret in sorted({api_key, quote(api_key, safe=""), quote_plus(api_key)}, key=len, reverse=True):
        if secret:
            value = value.replace(secret, "[REDACTED]")
    return value


def reject_nonfinite(value: str) -> None:
    raise ValueError(f"JSON 中存在非有限数值：{value}")


def captured_response(
    response: Any, api_key: str, content: bytes, *, complete: bool
) -> dict[str, Any]:
    # 保留完整正文、全部响应头和全部 JSON 字段；仅对密钥值脱敏。
    try:
        raw_body = redact(content.decode("utf-8"), api_key)
        encoded_body = None
    except UnicodeDecodeError:
        safe_bytes = content.replace(api_key.encode("utf-8"), b"[REDACTED]")
        raw_body = None
        encoded_body = base64.b64encode(safe_bytes).decode("ascii")
    body: Any = None
    if raw_body is not None:
        try:
            body = json.loads(raw_body.lstrip("\ufeff"), parse_constant=reject_nonfinite)
        except (ValueError, json.JSONDecodeError):
            pass
    result = {
        "httpStatus": response.getcode(),
        "complete": complete,
        "reason": redact(str(response.reason or ""), api_key),
        "headers": [[name, redact(value, api_key)] for name, value in response.headers.items()],
        "body": body,
        "rawBody": raw_body,
    }
    if encoded_body is not None:
        result["bodyBase64"] = encoded_body
    return result


def save_archive(path: Path, archive: dict[str, Any]) -> None:
    archive["updatedAt"] = iso_timestamp()
    atomic_write_json(path, archive)


def request_once(
    *,
    endpoint: str,
    params: dict[str, str],
    attraction_ids: list[str],
    attempt: int,
    api_key: str,
    archive: dict[str, Any],
    output_path: Path,
    timing: dict[str, float],
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "requestId": uuid.uuid4().hex,
        "queryFingerprint": request_fingerprint(endpoint, params),
        "attractionIds": attraction_ids,
        "attempt": attempt,
        "preparedAt": iso_timestamp(),
        "startedAt": None,
        "finishedAt": None,
        "outcome": "pending",
        "request": {
            "method": "GET",
            "endpoint": endpoint,
            "params": {**params, "key": "[REDACTED]"},
            "headers": dict(REQUEST_HEADERS),
        },
        "response": None,
    }
    archive["requests"].append(record)
    save_archive(output_path, archive)
    url = f"{endpoint}?{urlencode({**params, 'key': api_key})}"
    request = Request(url, headers=REQUEST_HEADERS)
    response = None
    chunks: list[bytes] = []
    complete = False
    try:
        remaining = MIN_REQUEST_INTERVAL_SECONDS - (time.monotonic() - timing["lastStarted"])
        if remaining > 0:
            time.sleep(remaining)
        timing["lastStarted"] = time.monotonic()
        record["startedAt"] = iso_timestamp()
        try:
            response = urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS)
        except HTTPError as error:
            # 4xx/5xx 的返回正文同样保存，不能只留一个错误码。
            response = error
        while True:
            # 逐块保留已收到的正文，中断时也能保存现有内容和响应头。
            chunk = response.read1(65536)
            if not chunk:
                break
            chunks.append(chunk)
        declared_length = response.headers.get("Content-Length")
        if (
            declared_length
            and declared_length.isdigit()
            and not response.headers.get("Transfer-Encoding")
            and int(declared_length) != sum(len(chunk) for chunk in chunks)
        ):
            raise OSError("高德响应正文长度不完整")
        complete = True
        record["outcome"] = "received"
    except KeyboardInterrupt:
        record["outcome"] = "interrupted"
        record["error"] = {"type": "KeyboardInterrupt", "message": "用户中断请求"}
        raise
    except IncompleteRead as error:
        chunks.append(error.partial)
        record["outcome"] = "retryable_error"
        record["error"] = {"type": "IncompleteRead", "message": "高德响应正文未接收完整"}
    except Exception as error:
        record["outcome"] = "retryable_error"
        record["error"] = {
            "type": type(error).__name__,
            "message": redact(str(error), api_key),
        }
    finally:
        if response is not None:
            record["response"] = captured_response(
                response, api_key, b"".join(chunks), complete=complete
            )
            try:
                response.close()
            except OSError:
                pass
        record["finishedAt"] = iso_timestamp()
        # 先完整落盘，再分类或提取。即使之后中断也能从正文恢复。
        save_archive(output_path, archive)
    return record


PROVINCE_LOOKUP = {alias: canonical for canonical, aliases in PROVINCE_ALIASES for alias in aliases}
PLACE_SUFFIXES = (
    "国家级旅游风景名胜区", "国家级旅游风景区", "国家旅游度假区", "国家重点风景名胜区", "国家级风景名胜区",
    "国家森林公园", "国家地质公园", "国家湿地公园", "森林公园", "地质公园",
    "风景名胜旅游区", "文化旅游景区", "旅游风景区", "风景名胜区", "文化旅游区", "旅游度假区",
    "生态旅游区", "旅游景区", "风景旅游区", "风景区", "旅游区", "游览区", "景区", "公园",
)
FACILITY_WORDS = ("公交站", "地铁站", "停车场", "售票", "游客中心", "游客服务", "旅游服务",
                  "卫生间", "酒店", "宾馆", "客栈", "餐厅", "购物", "管理处", "管理局",
                  "检票", "入口", "出口", "东门", "西门", "南门", "北门", "打卡点", "游客之家",
                  "宣誓大厅", "初心教室", "专题展厅", "主展厅")
MAX_SEARCH_PAGES = 4
ETHNIC_GROUPS = r"(?:藏族|羌族|土家族|苗族|布依族|哈尼族|彝族|傣族|景颇族|白族|壮族|侗族|傈僳族|蒙古族|蒙古|哈萨克|柯尔克孜族|朝鲜族|回族|黎族|瑶族|土族|纳西族|布朗族)"


def provider_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def canonical_province(value: Any) -> str:
    return PROVINCE_LOOKUP.get(provider_text(value), "")


def same_admin(expected: str, actual: str) -> bool:
    if expected == actual:
        return True
    # 文旅部使用“阿坝州、甘南州”等简称，高德使用自治州全称。
    for ending in ("州", "县", "旗"):
        if expected.endswith(ending) and actual.endswith("自治" + ending):
            stem = expected.removesuffix("自治" + ending).removesuffix(ending)
            ethnic = actual.removeprefix(stem).removesuffix("自治" + ending)
            return actual.startswith(stem) and bool(re.fullmatch(ETHNIC_GROUPS + "+", ethnic))
    return False


def remove_prefix(value: str, prefixes: list[str]) -> str:
    for prefix in sorted(set(prefixes), key=len, reverse=True):
        if prefix and value.startswith(prefix):
            return value[len(prefix):]
    return value


def administrative_context(item: dict[str, Any]) -> dict[str, str]:
    hints = item.get("provinceHints", [])
    if len(hints) != 1 or not canonical_province(hints[0]):
        raise DataError(f"{item['name']} 缺少唯一省份")
    province = canonical_province(hints[0])
    aliases = [key for key, value in PROVINCE_LOOKUP.items() if value == province]
    city = province if province in {"北京市", "天津市", "上海市", "重庆市", "澳门特别行政区", "香港特别行政区"} else ""
    district = ""
    for value in (item.get("regionText", ""), item["name"]):
        rest = remove_prefix(value.strip(), aliases)
        match = re.match(r"^(.{2,18}?(?:自治州|地区|盟|市))", rest)
        if not match:
            match = re.match(r"^(.{2,12}?州)", rest)
        if match and not any(word in match[1] for word in ("景区", "旅游", "博物")):
            if not city:
                city = match[1]
            if match[1] == city:
                rest = rest[len(match[1]):]
        if city:
            rest = remove_prefix(rest, [city])
        match = re.match(r"^(.{1,14}?(?:自治县|县区|旗|县|区|市))", rest)
        if match and not any(word in match[1] for word in ("景", "旅游", "游览", "休闲", "生态", "文化", "公园", "保护区", "开发区")):
            district = district or match[1]
    return {"province": province, "city": city, "district": district}


def local_name(value: str, context: dict[str, str]) -> str:
    # “(立园景区)”和“(马降龙景区)”是不同园区，不能当备注一起删除。
    value = re.sub(r"[（(]([^）)]*)[）)]", lambda match: match[1] if match[1].endswith(("景区", "园区")) else "", value).strip()
    aliases = [key for key, province in PROVINCE_LOOKUP.items() if province == context["province"]]
    value = re.sub(r"^(?:第?[一二三四五六七八九十\d]+师)?[一二三四五六七八九十\d]+团", "", value)
    prefixes = list(aliases)
    for field in ("city", "district"):
        part = context[field]
        prefixes.append(part)
        for suffix in ("自治州", "自治县", "地区", "州", "市", "县"):
            if part.endswith(suffix):
                prefixes.append(part[:-len(suffix)])
                if suffix.startswith("自治"):
                    prefixes.append(re.sub(ETHNIC_GROUPS + r"+$", "", part[:-len(suffix)]))
                break
    prefixes = [prefix for prefix in prefixes if len(prefix) >= 2]
    while True:
        shortened = remove_prefix(value, prefixes)
        core = name_without_generic_suffix(shortened)
        if shortened == value or len(core) < 2 or core in {"古城", "古镇", "遗址", "国家公园"} or shortened in PLACE_SUFFIXES:
            break
        value = shortened
    return re.sub(r"^(?:第?[一二三四五六七八九十\d]+师)?[一二三四五六七八九十\d]+团", "", value).strip()


def trim_place_suffix(value: str, *, preserve_kind: bool = False) -> str:
    for suffix in PLACE_SUFFIXES:
        if preserve_kind and "公园" in suffix:
            continue
        if value.endswith(suffix) and len(value) > len(suffix) + 1:
            return value[:-len(suffix)]
    return value


def name_without_generic_suffix(value: str) -> str:
    previous = None
    while previous != value:
        previous = value
        for suffix in PLACE_SUFFIXES:
            if value.endswith(suffix) and len(value) > len(suffix):
                value = value[:-len(suffix)]
                break
    return value


def name_key(value: str, context: dict[str, str], *, preserve_kind: bool = False) -> str:
    value = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", local_name(value, context)).lower()
    value = value.replace("中科院", "中国科学院")
    value = value.replace("影视城", "影城")
    value = value.replace("城址", "城遗址")
    # 遗址公园是遗址的保护展示形式；不把博物馆、度假区混作遗址本体。
    value = re.sub(r"(?:国家)?(?:考古)?遗址公园$", "遗址", value)
    value = value.replace("国家重点风景名胜区", "风景名胜区")
    previous = None
    while value != previous:
        previous = value
        value = trim_place_suffix(value, preserve_kind=preserve_kind)
    if not preserve_kind:
        for suffix in ("古村落", "古村", "古镇", "古城", "遗址"):
            if value.endswith(suffix) and len(value) > 3:
                value = value[:-len(suffix)]
    return value


def lookup_item(item: dict[str, Any]) -> dict[str, Any]:
    point = item.get("representativePoint")
    if not point:
        return item
    return {**item, "name": point["name"], "aliases": [], "nameEvidence": []}


def literal_name(value: str, context: dict[str, str]) -> str:
    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", local_name(value, context)).lower()


def search_parameters(item: dict[str, Any]) -> list[dict[str, str]]:
    item = lookup_item(item)
    context = administrative_context(item)
    names = [item["name"], *item.get("aliases", [])]
    keywords = [local_name(name, context) for name in names]
    keywords += [trim_place_suffix(local_name(name, context)) for name in names]
    keywords.append(name_key(item["name"], context))
    result = []
    for keyword in dict.fromkeys(keywords):
        if len(keyword) < 2:
            continue
        params = {"keywords": keyword, "output": "JSON", "extensions": "all",
                  "children": "1", "offset": "25", "page": "1"}
        if context["city"]:
            params.update(city=context["city"], citylimit="true")
        elif context["province"]:
            # 不把未确认的城市猜进去；缺城市时用省份限定关键词，再核对结果省份。
            params["keywords"] = f"{context['province']}{keyword}"
        if params not in result:
            result.append(params)
    # 先查景点、文化场馆及休闲地点，避免同名酒店和餐馆淹没景点。
    # 自然地名、村寨等不在这些分类时，再查不限定类型的结果。
    result = result[:3]
    scenic = [{**params, "types": "110202"} for params in result] if any(
        classification.get("type") == "a-level" for classification in item.get("classifications", [])
    ) else []
    precise = {"keywords": f"{context['district']}{local_name(item['name'], context)}",
               "output": "JSON", "extensions": "all", "children": "1", "offset": "25", "page": "1"}
    if context["city"]:
        precise.update(city=context["city"], citylimit="true")
    else:
        precise["keywords"] = f"{context['province']}{precise['keywords']}"
    venue_searches = []
    if item.get("verifiedVenue"):
        # 商业景区里的品牌店很多，单独搜索整体场所分类，仍须等分页完整才接受。
        for name in item["verifiedVenue"]["names"]:
            params = {**precise, "keywords": name,
                      "types": "商场|特色商业街|产业园区|科教文化服务|休闲场所|农林牧渔基地"}
            venue_searches.append(params)
    return venue_searches + scenic + [{**params, "types": "风景名胜|科教文化服务|体育休闲服务"} for params in result] + result + ([precise] if precise not in result else [])


def source_fingerprint(item: dict[str, Any]) -> str:
    material = {key: item.get(key) for key in ("id", "name", "aliases", "nameEvidence", "representativePoint", "regionText", "provinceHints", "geocodeQuery", "reviewStatus")}
    if item.get("verifiedVenue") is not None:
        material.update(verifiedVenue=item["verifiedVenue"], classifications=item.get("classifications"))
    return hashlib.sha256(json.dumps(material, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def venue_text(value: str) -> str:
    # 保留括号内的分店、楼层等实质文字，不用普通景点的宽松名称归一化。
    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", unicodedata.normalize("NFKC", value)).lower()


def venue_address(value: str, context: dict[str, str]) -> str:
    value = re.sub(r"[（(][^）)]*[）)]", "", value).strip()
    previous = None
    while previous != value:
        previous = value
        value = remove_prefix(value, list(context.values()))
    return venue_text(value)


def verified_venue_match(item: dict[str, Any], candidate: dict[str, Any], raw: dict[str, Any],
                         context: dict[str, str]) -> bool:
    venue = item.get("verifiedVenue")
    if not venue or candidate["source"] != "poi" or candidate["parentPoiId"]:
        return False
    if not all(context.values()) or not any(c.get("type") == "a-level" for c in item.get("classifications", [])):
        return False
    # 只允许整体场所分类；即使在同址，内部商店、酒店、停车场仍不能代替景区。
    if any(word in candidate["name"] for word in FACILITY_WORDS if word != "购物"):
        return False
    kinds = set(re.split(r"[;|]", candidate["type"]))
    if not kinds.intersection({"商场", "特色商业街", "产业园区", "科教文化场所", "休闲场所", "农林牧渔基地"}):
        return False
    names = [candidate["name"], *re.split(r"[;；|]", provider_text(raw.get("alias")))]
    return bool(
        {venue_text(name) for name in names if name} & {venue_text(name) for name in venue["names"]}
        and venue_address(candidate["address"], context)
        in {venue_address(address, context) for address in venue["addresses"]}
    )


def candidate_location(candidate: dict[str, Any]) -> dict[str, Any]:
    try:
        lng, lat = map(float, candidate["location"].split(","))
    except (AttributeError, KeyError, TypeError, ValueError):
        raise DataError("坐标格式无效") from None
    if not (math.isfinite(lng) and math.isfinite(lat) and 70 <= lng <= 140 and 0 <= lat <= 60):
        raise DataError("坐标超出中国合理范围")
    if not re.fullmatch(r"\d{6}", candidate["adcode"]):
        raise DataError("行政编码无效")
    return {"lng": lng, "lat": lat, **{key: candidate[key] for key in ("province", "city", "district", "adcode", "address")}}


def candidates_from_record(record: dict[str, Any], item: dict[str, Any]) -> list[dict[str, Any]]:
    if classify_response(record)[0] != "success":
        return []
    poi = record["request"]["endpoint"] == POI_ENDPOINT
    point = item.get("representativePoint")
    item = lookup_item(item)
    context = administrative_context(item)
    keys = {name_key(name, context) for name in [item["name"], *item.get("aliases", [])]}
    keys = {key for key in keys if len(key) >= 2}
    values = record["response"]["body"]["pois" if poi else "geocodes"]
    result = []
    for index, raw in enumerate(values):
        if not isinstance(raw, dict):
            continue
        name = provider_text(raw.get("name" if poi else "formatted_address"))
        candidate = {
            "requestId": record["requestId"], "candidateIndex": index,
            "poiId": provider_text(raw.get("id")) if poi else "", "source": "poi" if poi else "geocode",
            "parentPoiId": provider_text(raw.get("parent")) if poi else "",
            "name": name, "type": provider_text(raw.get("type" if poi else "level")),
            "province": canonical_province(raw.get("pname" if poi else "province")),
            "city": provider_text(raw.get("cityname" if poi else "city")),
            "district": provider_text(raw.get("adname" if poi else "district")),
            "adcode": provider_text(raw.get("adcode")), "location": provider_text(raw.get("location")),
            "address": provider_text(raw.get("address" if poi else "formatted_address")),
        }
        reason = ""
        candidate["nameMatch"] = "normalized"
        candidate["namePriority"] = 0
        verified_venue = verified_venue_match(item, candidate, raw, context)
        exact_point = bool(point and literal_name(name, context) == literal_name(point["name"], context))
        # 仅对事先核实的具名代表点放行，不放宽其他设施的匹配规则。
        point_type = bool(exact_point and (
            (point["kind"] == "visitor-center" and poi and "服务中心" in name and "服务" in candidate["type"])
            or (point["kind"] in {"historic-gate", "landmark", "venue"} and poi and "风景名胜" in candidate["type"])
            or (point["kind"] == "boarding-point" and poi and "码头" in name
                and any(kind in candidate["type"] for kind in ("码头", "风景名胜")))
            or (point["kind"] == "village" and (
                (not poi and candidate["type"] == "村庄")
                or (poi and any(kind in candidate["type"] for kind in ("地名", "行政", "风景")))
            ))
        ))
        if candidate["province"] != context["province"]:
            reason = "省份不一致"
        elif context["city"] and not (same_admin(context["city"], candidate["city"]) or same_admin(context["city"], candidate["district"])):
            reason = "城市不一致"
        elif context["district"] and not same_admin(context["district"], candidate["district"]):
            reason = "区县不一致"
        elif any(word in name for word in FACILITY_WORDS) and not (point_type or verified_venue):
            reason = "匹配到公交、停车或商业服务设施"
        elif item.get("verifiedVenue") and not verified_venue:
            reason = "不是已核实的景区整体地点（名称、地址或类型不符）"
        elif point and not point_type:
            reason = "不是已核实的具名代表点或代表点类型不符"
        elif not poi and candidate["type"] != "兴趣点" and not point_type:
            reason = f"仅匹配到{candidate['type'] or '未知级别'}"
        if not reason:
            raw_names = [name]
            if poi:
                raw_names += re.split(r"[;；|]", provider_text(raw.get("alias")))
                parts = re.split(r"[-－—·•]", name)
                groups = [classification.get("groupName", "") for classification in item.get("classifications", [])
                          if classification.get("type") == "a-level" and classification.get("scope") == "component"]
                if len(parts) > 1:
                    known_parent = any(name_key(parts[0], context) == name_key(group, context) for group in groups if group)
                    if candidate["parentPoiId"] or known_parent:
                        raw_names.append(parts[-1])
                # 已由官方资料确认的组成关系也可能没有连字符，如“赤水丹霞旅游区燕子岩”。
                for group in groups:
                    parent = local_name(group, context)
                    local = local_name(name, context)
                    if parent and local.startswith(parent):
                        child = local[len(parent):].strip("-－—·•（）() ")
                        if child:
                            raw_names.append(child)
            else:
                raw_names = [remove_prefix(name, ["中国"])]
            actual_context = {key: candidate[key] for key in ("province", "city", "district")}
            candidate_keys = {name_key(value, scope) for value in raw_names if value for scope in (context, actual_context)}
            comparable_keys = keys | {name_key(value, actual_context) for value in [item["name"], *item.get("aliases", [])]}
            comparable_keys = {key for key in comparable_keys if len(key) >= 2 and key not in PLACE_SUFFIXES}
            # 地理编码只有地址字段，允许前面带乡镇，但不允许后面附带内部设施名。
            same_name = (verified_venue or bool(comparable_keys & candidate_keys)) if poi else any(
                candidate_key.endswith(key) for candidate_key in candidate_keys for key in comparable_keys
            )
            if not same_name:
                reason = "名称或别名不能明确对应"
            else:
                precise_keys = {name_key(value, scope, preserve_kind=True) for value in [item["name"], *item.get("aliases", [])] for scope in (context, actual_context)}
                precise_candidates = {name_key(value, scope, preserve_kind=True) for value in raw_names if value for scope in (context, actual_context)}
                if precise_keys & precise_candidates:
                    candidate["nameMatch"] = "exact-alias"
                    candidate["namePriority"] = 1
                    main_keys = {name_key(name, scope, preserve_kind=True) for scope in (context, actual_context)}
                    if precise_keys & main_keys:
                        candidate["nameMatch"] = "exact-name"
                        candidate["namePriority"] = 2
                    # 不能为每个候选删除它自己独有的区县前缀后，再当作原名精确匹配。
                    direct_keys = {name_key(value, context, preserve_kind=True) for value in [item["name"], *item.get("aliases", [])]}
                    if name_key(name, context, preserve_kind=True) in direct_keys:
                        candidate["nameMatch"] = "exact-source-name"
                        candidate["namePriority"] = 3
                    verified_names = [alias for evidence in item.get("nameEvidence", []) for alias in evidence["aliases"]]
                    if literal_name(name, context) in {literal_name(alias, context) for alias in verified_names}:
                        candidate["nameMatch"] = "verified-official-name"
                        candidate["namePriority"] = 4
                if point_type:
                    candidate.update(nameMatch="verified-representative", namePriority=4,
                                     locationRole=point["kind"])
                if verified_venue:
                    candidate.update(nameMatch="verified-venue", namePriority=5, locationRole="whole-venue")
        if not reason and poi:
            kind = candidate["type"]
            is_attraction = any(word in kind for word in ("风景名胜", "博物馆", "纪念馆", "展览馆", "科技馆", "美术馆", "公园", "动物园", "植物园", "游乐场"))
            is_village = any(word in item["name"] for word in ("村", "古镇", "古寨", "大寨")) and any(word in kind for word in ("地名", "行政", "风景"))
            is_landform = "自然地名" in kind and any(word in item["name"] for word in ("山", "湖", "河", "峡", "梯田", "茶林"))
            is_heritage = any(c.get("type") == "world-heritage" for c in item.get("classifications", []))
            is_heritage_venue = is_heritage and candidate["namePriority"] >= 2 and (
                ("剧院" in item["name"] and any(word in kind for word in ("剧场", "剧院")))
                or ("前地" in item["name"] and any(word in kind for word in ("广场", "地名地址")))
            )
            if not (is_attraction or is_village or is_landform or is_heritage_venue or point_type or verified_venue):
                reason = "地点类型尚不能确认是该景点"
            elif not candidate["poiId"]:
                reason = "缺少地点 ID"
        if not reason:
            try:
                candidate_location(candidate)
            except DataError as error:
                reason = str(error)
        candidate["rejectionReason"] = reason
        result.append(candidate)
    return result


def matching_state(item: dict[str, Any], available: dict[str, dict[str, Any]],
                   *, history: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    binding: dict[str, Any] = {
        "id": item["id"], "name": item["name"], "reviewStatus": item["reviewStatus"],
        "sourceFingerprint": source_fingerprint(item), "matchStatus": "pending", "matchReason": "",
        "selected": None, "candidates": [], "requestIds": [], "nextRequests": [],
    }
    if item["reviewStatus"] != "ready":
        binding.update(matchStatus=item["reviewStatus"], matchReason=item.get("reviewReason") or item.get("skipReason", ""))
        return binding
    records = []
    complete_request_ids: set[str] = set()
    old = available.get(query_fingerprint(item["geocodeQuery"]))
    if old:
        records.append(old)
        complete_request_ids.add(old["requestId"])
    truncated = False
    searches = search_parameters(item)
    # 匹配规则调整后仍复核这个景点的历史 POI 返回，不因关键词整理变化丢弃已有数据。
    for record in available.values() if history is None else history:
        if record["request"]["endpoint"] == POI_ENDPOINT and {item["id"], *item.get("sourceRecordIds", [])}.intersection(record.get("attractionIds", [])):
            initial = {key: value for key, value in record["request"]["params"].items() if key != "key"}
            initial["page"] = "1"
            if initial not in searches:
                searches.append(initial)
    for initial in searches:
        group_records = []
        district = administrative_context(item)["district"]
        page_limit = 40 if district and initial["keywords"].startswith(district) and not initial.get("types") else MAX_SEARCH_PAGES
        for page in range(1, page_limit + 1):
            params = {**initial, "page": str(page)}
            record = available.get(request_fingerprint(POI_ENDPOINT, params))
            if not record:
                binding["nextRequests"].append(params)
                break
            records.append(record)
            group_records.append(record)
            outcome = classify_response(record)[0]
            if outcome != "success":
                if outcome == "no_result":
                    complete_request_ids.update(value["requestId"] for value in group_records)
                break
            # count 在实际返回中会随页码变化，甚至为 0 但仍有 POI；以当前页是否取满为准。
            if len(record["response"]["body"]["pois"]) < int(params["offset"]):
                complete_request_ids.update(value["requestId"] for value in group_records)
                break
            if page == page_limit:
                truncated = True
    candidates = [candidate for record in records for candidate in candidates_from_record(record, item)]
    accepted = [candidate for candidate in candidates if not candidate["rejectionReason"] and candidate["requestId"] in complete_request_ids]
    # 有明确 POI 时优先采用 POI，避免同一地点的旧地址坐标被当成第二个候选。
    pois = [candidate for candidate in accepted if candidate["source"] == "poi"]
    # 景区本体与同名子设施同时出现时，使用已有明确父子关系的景区本体。
    poi_ids = {candidate["poiId"] for candidate in pois}
    pois = [candidate for candidate in pois if candidate["parentPoiId"] not in poi_ids]
    attractions = [candidate for candidate in pois if "自然地名" not in candidate["type"]]
    if attractions:
        pois = attractions
    accepted = pois or accepted
    # 不同实体只有去掉“地质公园”等实质词后才同名时，优先保留名称/别名精确对应者。
    if accepted:
        priority = max(candidate["namePriority"] for candidate in accepted)
        accepted = [candidate for candidate in accepted if candidate["namePriority"] == priority]
        # 同名景区本体优于景区中的普通旅游标记；多个同级景区本体仍需确认。
        landmarks = [candidate for candidate in accepted if candidate["namePriority"] >= 2 and
                     any(kind in candidate["type"] for kind in ("国家级景点", "世界遗产"))]
        if landmarks:
            accepted = landmarks
    distinct = {}
    for candidate in accepted:
        key = candidate["poiId"] or (candidate["location"], candidate["adcode"])
        distinct.setdefault(key, candidate)
    binding["requestIds"] = list(dict.fromkeys(record["requestId"] for record in records))
    binding["candidates"] = list(distinct.values()) or candidates[:10]
    if len(distinct) == 1:
        selected = next(iter(distinct.values()))
        reason = ("官方 A 级景区整体地点的名称、地区和地址已核实，候选唯一"
                  if selected.get("nameMatch") == "verified-venue" else "名称、地区和类型匹配，候选唯一")
        binding.update(matchStatus="matched", matchReason=reason, selected=selected)
    elif len(distinct) > 1:
        binding.update(matchStatus="needs_review", matchReason=f"有 {len(distinct)} 个同名合格地点，需确认具体游览地点")
    elif binding["nextRequests"]:
        binding.update(matchStatus="pending", matchReason="尚需补充 POI 搜索或分页")
    elif truncated:
        binding.update(matchStatus="needs_review", matchReason="部分搜索超过分页上限，需要更具体的名称")
    else:
        binding.update(matchStatus="needs_review", matchReason="已查询，未找到名称、地区、类型均能确认的地点")
    return binding


def update_summary(archive: dict[str, Any]) -> dict[str, int]:
    summary = {
        "total": len(archive["items"]),
        "matched": 0,
        "pending": 0,
        "needsReview": 0,
        "skipped": 0,
        "historicalRequests": len(archive["requests"]),
        "successfulRequests": sum(classify_response(record)[0] == "success" for record in archive["requests"]),
    }
    for item in archive["items"]:
        field = {"matched": "matched", "needs_review": "needsReview", "skip": "skipped"}.get(item["matchStatus"], "pending")
        summary[field] += 1
    archive["summary"] = summary
    return summary


def collect(input_path: Path, output_path: Path, only_ids: list[str] | None = None) -> int:
    paths = [input_path.resolve(), output_path.resolve(), LOCK_PATH.resolve()]
    if len(set(paths)) != len(paths):
        raise DataError("输入、输出和内部锁文件的路径不能相同")
    source = read_source(input_path)
    selected_ids = set(only_ids or [item["id"] for item in source["items"]])
    unknown = selected_ids - {item["id"] for item in source["items"]}
    if unknown:
        raise DataError(f"景点 ID 不存在：{', '.join(sorted(unknown))}")
    with collector_lock():
        archive = read_archive(output_path) if output_path.exists() else new_archive()
        archive["input"] = str(input_path.resolve())
        archive["sourceGeneratedAt"] = source.get("generatedAt", "")
        available = reusable_requests(archive)
        archive["items"] = [matching_state(item, available) for item in source["items"]]
        bindings = {item["id"]: item for item in archive["items"]}
        pending = [item for item in source["items"] if item["id"] in selected_ids and item["reviewStatus"] == "ready"
                   and bindings[item["id"]]["matchStatus"] != "matched" and bindings[item["id"]]["nextRequests"]]
        api_key = os.environ.get("AMAP_WEB_SERVICE_KEY", "").strip()
        if pending and not api_key:
            raise DataError("缺少 AMAP_WEB_SERVICE_KEY，无法执行尚未采集的查询")

        update_summary(archive)
        save_archive(output_path, archive)
        print(f"已从保存的数据确认 {archive['summary']['matched']} 个景点，本次待补查 {len(pending)} 个景点。", flush=True)
        timing = {"lastStarted": time.monotonic()}
        aborted = False
        try:
            for index, item in enumerate(pending, 1):
                print(f"[{index}/{len(pending)}] {item['name']}", flush=True)
                binding = bindings[item["id"]]
                binding.update(matching_state(item, available))
                while binding["matchStatus"] != "matched" and binding["nextRequests"]:
                    params = binding["nextRequests"][0]
                    for attempt in range(len(RETRY_BACKOFF_SECONDS) + 1):
                        record = request_once(endpoint=POI_ENDPOINT, params=params, attraction_ids=[item["id"]],
                                              attempt=attempt + 1, api_key=api_key, archive=archive,
                                              output_path=output_path, timing=timing)
                        outcome, message = classify_response(record)
                        record.update(outcome=outcome, message=message)
                        if outcome in REUSABLE_OUTCOMES:
                            available[record["queryFingerprint"]] = record
                            break
                        if outcome == "fatal_error" or attempt == len(RETRY_BACKOFF_SECONDS):
                            print(f"采集停止：{message}", file=sys.stderr)
                            aborted = True
                            break
                        time.sleep(RETRY_BACKOFF_SECONDS[attempt])
                    binding.update(matching_state(item, available))
                    update_summary(archive)
                    save_archive(output_path, archive)
                    if aborted:
                        break
                print(f"  {binding['matchStatus']}：{binding['matchReason']}", flush=True)
                if aborted:
                    break
        except KeyboardInterrupt:
            print("\n采集已中断，已保存的请求和响应会在下次复用。", file=sys.stderr)
            aborted = True
        finally:
            summary = update_summary(archive)
            save_archive(output_path, archive)

        print(f"高德原始数据：{output_path}")
        print(
            f"请求成功 {summary['successfulRequests']} 次；景点确认 {summary['matched']} 条，"
            f"待补查 {summary['pending']} 条，待确认 {summary['needsReview']} 条，跳过 {summary['skipped']} 条。"
        )
        unresolved = [item for item in archive["items"] if item["id"] in selected_ids and item["matchStatus"] not in {"matched", "skip"}]
        return int(aborted or bool(unresolved))


def main() -> int:
    parser = argparse.ArgumentParser(description="保存每次高德请求和完整响应，支持断点续跑")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_PATH, help="景点基础资料 JSON")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH, help="高德原始数据 JSON")
    parser.add_argument("--id", action="append", dest="only_ids", help="只补查此景点 ID，可重复指定；仍保留全部记录")
    args = parser.parse_args()
    try:
        return collect(args.input, args.output, args.only_ids)
    except DataError as error:
        print(f"高德采集未完成：{error}", file=sys.stderr)
        return 2
    except OSError:
        print("高德原始数据文件无法读写，采集已停止。", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\n采集已中断，重新执行同一命令可继续。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
