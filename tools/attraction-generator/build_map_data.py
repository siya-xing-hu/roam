#!/usr/bin/env python3
"""只读取本地各来源数据，生成前端使用的 attractions.json。"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys
from typing import Any

from geocode_amap import (
    DataError,
    DEFAULT_INPUT_PATH,
    DEFAULT_OUTPUT_PATH as DEFAULT_AMAP_PATH,
    atomic_write_json,
    candidate_location,
    iso_timestamp,
    matching_state,
    provider_text,
    read_archive,
    read_source,
    reusable_requests,
    source_fingerprint,
)


TOOL_ROOT = Path(__file__).resolve().parent
DEFAULT_MAP_PATH = TOOL_ROOT.parent.parent / "public/data/attractions.json"


def display_classification(item: dict[str, Any]) -> tuple[int, list[str]]:
    classifications = item.get("classifications")
    if not isinstance(classifications, list) or not classifications:
        raise DataError("缺少景点分类")
    level = 0
    tags: list[str] = []
    for classification in classifications:
        if not isinstance(classification, dict):
            raise DataError("景点分类格式无效")
        kind, value = classification.get("type"), classification.get("value")
        if kind == "a-level":
            if not isinstance(value, str) or not re.fullmatch(r"[1-5]A", value):
                raise DataError("A 级景区等级无效")
            level = max(level, int(value[0]))
            tags.append(f"{value}景区组成部分" if classification.get("scope") == "component" else value)
        elif kind == "world-heritage":
            if value not in {"cultural", "mixed"}:
                raise DataError("世界遗产类型无效")
            label = "世界文化与自然双遗产" if value == "mixed" else "世界文化遗产"
            scope = classification.get("scope")
            if scope == "contains-component":
                tags.append(f"含{label}组成部分")
            elif scope == "viewpoint":
                tags.append(f"{label}沿线观景地")
            else:
                tags.append(label)
            group_name = classification.get("groupName") or classification.get("sourceName")
            if isinstance(group_name, str) and group_name and group_name not in tags:
                tags.append(group_name)
    # 没有 A 级的世界遗产保留标签，展示等级按现有前端规则为 0（未评级）。
    return level, list(dict.fromkeys(tags))


def map_item(item: dict[str, Any], selected: dict[str, Any]) -> dict[str, Any]:
    location = candidate_location(selected)
    level, tags = display_classification(item)
    sources = item.get("sources")
    if not isinstance(sources, list) or not sources or not isinstance(sources[0], dict):
        raise DataError("缺少官方来源")
    source = sources[0]
    if not provider_text(source.get("name")) or not provider_text(source.get("url")):
        raise DataError("官方来源缺少名称或网址")
    point = item.get("representativePoint")
    if point:
        tags.append(f"坐标代表点：{point['label']}")
    return {
        "id": item["id"],
        "name": f"{item['name']}（{point['label']}）" if point else item["name"],
        **location,
        "level": level,
        "levelSource": "official",
        "tags": tags,
        "source": {
            "name": source["name"],
            "url": source["url"],
            "publishedAt": provider_text(source.get("publishedAt")),
        },
    }


def build(base_path: Path, amap_path: Path, output_path: Path) -> int:
    resolved_paths = [path.resolve() for path in (base_path, amap_path, output_path)]
    if len(set(resolved_paths)) != len(resolved_paths):
        raise DataError("基础资料、高德原始数据和地图输出不能使用相同路径")
    source = read_source(base_path)
    archive = read_archive(amap_path)
    available = reusable_requests(archive)
    bindings: dict[str, dict[str, Any]] = {}
    for binding in archive["items"]:
        if not isinstance(binding, dict) or not isinstance(binding.get("id"), str):
            raise DataError("高德原始数据缺少景点 ID 关联")
        if binding["id"] in bindings:
            raise DataError(f"高德原始数据含重复景点 ID：{binding['id']}")
        bindings[binding["id"]] = binding

    output_items: list[dict[str, Any]] = []
    errors: list[str] = []
    selected_pois: dict[str, str] = {}
    skipped = 0
    for item in source["items"]:
        if item["reviewStatus"] == "skip":
            skipped += 1
            continue
        try:
            if item["reviewStatus"] == "needs_review":
                raise DataError(item.get("reviewReason") or "需要先确认基础资料")
            binding = bindings.get(item["id"])
            if binding is None or binding.get("sourceFingerprint") != source_fingerprint(item):
                raise DataError("高德数据尚未关联此景点或查询已改变，请先运行高德采集")
            match = matching_state(item, available)
            if match["matchStatus"] != "matched":
                raise DataError(match["matchReason"])
            poi_id = match["selected"].get("poiId")
            if poi_id:
                if poi_id in selected_pois:
                    raise DataError(f"与 {selected_pois[poi_id]} 对应同一高德地点，需核对景点是否重复")
                selected_pois[poi_id] = item["name"]
            output_items.append(map_item(item, match["selected"]))
        except DataError as error:
            errors.append(f"{item['id']} {item['name']}：{error}")

    if errors:
        print(f"地图数据未生成，{len(errors)} 条记录需要处理：", file=sys.stderr)
        for message in errors:
            print(f"- {message}", file=sys.stderr)
        print("现有地图数据保留。", file=sys.stderr)
        return 1
    if not output_items:
        raise DataError("没有可显示的景点，保留现有地图数据")

    output_items.sort(key=lambda item: (-item["level"], item["name"], item["id"]))
    dataset = {
        "schemaVersion": 1,
        "generatedAt": iso_timestamp(),
        "coordSystem": "GCJ-02",
        "coverage": sorted({item["province"] for item in output_items}),
        "items": output_items,
    }
    atomic_write_json(output_path, dataset)
    print(f"已生成 {len(output_items)} 条地图数据（跳过 {skipped} 条）：{output_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="从本地基础资料和高德原始响应整合地图数据，不联网")
    parser.add_argument("--base", type=Path, default=DEFAULT_INPUT_PATH, help="景点基础资料 JSON")
    parser.add_argument("--amap", type=Path, default=DEFAULT_AMAP_PATH, help="高德原始数据 JSON")
    parser.add_argument("--output", type=Path, default=DEFAULT_MAP_PATH, help="地图数据 JSON")
    args = parser.parse_args()
    try:
        return build(args.base, args.amap, args.output)
    except DataError as error:
        print(f"地图数据未生成：{error}", file=sys.stderr)
        return 2
    except OSError:
        print("地图数据文件无法读写。", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
