#!/usr/bin/env python3
"""在完整原始响应中查找可复用 POI，只增加关联，不调用接口或改写响应。"""

import argparse
from collections import defaultdict
from pathlib import Path
import sys

from geocode_amap import (
    DataError, POI_ENDPOINT, administrative_context, atomic_write_json,
    candidates_from_record, canonical_province, classify_response,
    merge_reusable_requests, name_key, new_archive, provider_text,
    read_archive, read_source,
)


def link(source_path: Path, archives: list[Path], output: Path) -> dict:
    if output.resolve() in {path.resolve() for path in [source_path, *archives]}:
        raise DataError("复用关联必须保存到单独文件，不能覆盖原件")
    available = {}
    for path in archives:
        merge_reusable_requests(available, read_archive(path))
    index = defaultdict(set)
    for fingerprint, record in available.items():
        if record["request"]["endpoint"] != POI_ENDPOINT or classify_response(record)[0] != "success":
            continue
        for poi in record["response"]["body"]["pois"]:
            context = {"province": canonical_province(poi.get("pname")),
                       "city": provider_text(poi.get("cityname")), "district": provider_text(poi.get("adname"))}
            if not context["province"]:
                continue
            for scope in (context, {**context, "city": "", "district": ""}):
                key = name_key(provider_text(poi.get("name")), scope, preserve_kind=True)
                if len(key) >= 2:
                    index[context["province"], key].add(fingerprint)
    linked, item_ids, associations = {}, set(), 0
    for item in read_source(source_path)["items"]:
        if item["reviewStatus"] != "ready":
            continue
        context = administrative_context(item)
        keys = {name_key(name, context, preserve_kind=True) for name in [item["name"], *item.get("aliases", [])]}
        fingerprints = set().union(*(index.get((context["province"], key), set()) for key in keys))
        for fingerprint in sorted(fingerprints):
            record = available[fingerprint]
            if item["id"] in record.get("attractionIds", []):
                continue
            candidates = candidates_from_record(record, item)
            if not any(not value["rejectionReason"] and value["namePriority"] >= 3 for value in candidates):
                continue
            # 保留全部完整响应，包括同名候选。发布器仍会复核分页完整性和候选唯一性。
            value = linked.setdefault(fingerprint, {**record, "attractionIds": list(record.get("attractionIds", []))})
            value["attractionIds"].append(item["id"])
            item_ids.add(item["id"])
            associations += 1
    archive = new_archive()
    archive.update(requests=list(linked.values()), derivation={
        "kind": "offline-poi-associations", "source": str(source_path),
        "archives": [str(path) for path in archives], "linkedItems": len(item_ids), "associations": associations,
        "note": "完整响应原文未修改，仅为正式名录中名称、地区、类型相符的景点补充缓存关联。"})
    atomic_write_json(output, archive)
    print(f"本地复用：{len(item_ids)} 个景点，{associations} 条关联，未请求接口。")
    return archive


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--archive", type=Path, action="append", default=[])
    parser.add_argument("--archive-dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    archives = [*args.archive, *(sorted(args.archive_dir.glob("amap-*.json")) if args.archive_dir else [])]
    try:
        link(args.source, archives, args.output)
        return 0
    except (DataError, OSError, ValueError) as error:
        print(f"缓存复用未完成：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
