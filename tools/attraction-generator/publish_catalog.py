#!/usr/bin/env python3
"""增量发布已核验景点，保留已有地图并单独输出未解决的记录。"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
import sys

from build_map_data import map_item
from geocode_amap import (DataError, atomic_write_json, iso_timestamp, matching_state,
                          read_archive, read_source, merge_reusable_requests)


def publish(source_path: Path, archives: list[Path], output: Path, report_path: Path) -> dict:
    if len({path.resolve() for path in [source_path, output, report_path, *archives]}) != len(archives) + 3:
        raise DataError("来源、存档、地图与报告必须使用不同路径")
    source = read_source(source_path)
    before_bytes = output.read_bytes()
    before = json.loads(before_bytes)
    if before.get("schemaVersion") != 1 or before.get("coordSystem") != "GCJ-02":
        raise DataError("现有地图格式不受支持")
    original = {item["id"]: item for item in before["items"]}
    if len(original) != len(before["items"]):
        raise DataError("现有地图包含重复 ID")
    available = {}
    for path in archives:
        merge_reusable_requests(available, read_archive(path))
    history = defaultdict(set)
    for fingerprint, record in available.items():
        for attraction_id in record.get("attractionIds", []):
            history[attraction_id].add(fingerprint)
    matches = {}
    for item in source["items"]:
        keys = set().union(*(history.get(key, set()) for key in [item["id"], *item.get("sourceRecordIds", [])]))
        matches[item["id"]] = matching_state(item, available, history=[available[key] for key in sorted(keys)])
    # 已上图地点优先拥有对应 POI，新来源不能覆盖其身份或借用同一 POI 重复上图。
    poi_owners = {}
    for item_id in original:
        match = matches.get(item_id)
        if match and match["matchStatus"] == "matched":
            poi_id = match["selected"]["poiId"]
            if not poi_id:
                continue
            if poi_id in poi_owners:
                raise DataError("已有景点匹配到同一个 POI，需先核实")
            poi_owners[poi_id] = item_id
    result = dict(original)
    pending, additions, updates = [], [], []
    for item in source["items"]:
        match = matches[item["id"]]
        if match["matchStatus"] == "skip":
            continue
        reason = match["matchReason"]
        status = match["matchStatus"]
        if status == "matched":
            selected = match["selected"]
            poi_id = selected["poiId"]
            owner = poi_owners.get(poi_id) if poi_id else None
            if not poi_id and item["id"] not in original:
                status = "needs_review"
                reason = "仅有地址定位结果，需进一步核实景区 POI"
            elif owner and owner != item["id"]:
                status = "duplicate_poi"
                reason = f"与已保留景点 {result[owner]['name']} 匹配到同一地点，需核对是否同一实体"
            else:
                value = map_item(item, selected)
                if item["id"] not in original:
                    additions.append(item["id"])
                elif value != original[item["id"]]:
                    updates.append(item["id"])
                result[item["id"]] = value
                if poi_id:
                    poi_owners[poi_id] = item["id"]
                continue
        pending.append({"id": item["id"], "name": item["name"], "regionText": item["regionText"],
                        "status": status, "reason": reason, "alreadyPublished": item["id"] in original,
                        "sources": item["sources"], "classifications": item["classifications"]})
    items = sorted(result.values(), key=lambda item: (-item["level"], item["name"], item["id"]))
    if not set(original).issubset(result):
        raise DataError("增补结果丢失已有景点")
    now = iso_timestamp()
    dataset = {**before, "generatedAt": now, "coverage": sorted({item["province"] for item in items}), "items": items}
    summary = {"before": len(original), "after": len(items), "added": len(additions), "updated": len(updates),
               "unresolved": len(pending), "statuses": dict(Counter(item["status"] for item in pending)),
               "levels": dict(Counter(item["level"] for item in items))}
    report = {"generatedAt": now, "source": str(source_path), "output": str(output),
              "beforeSha256": sha256(before_bytes).hexdigest(), "summary": summary,
              "addedIds": additions, "updatedIds": updates,
              "provinces": {name: {"before": sum(item["province"] == name for item in original.values()),
                                   "after": sum(item["province"] == name for item in items)}
                            for name in dataset["coverage"]}, "unresolved": pending}
    # 若采集期间其他任务修改地图，停止而不是覆盖新修改。
    if output.read_bytes() != before_bytes:
        raise DataError("地图已被其他任务修改，请重新运行增补发布")
    atomic_write_json(report_path, report)
    atomic_write_json(output, dataset)
    print(json.dumps(summary, ensure_ascii=False))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--archive", type=Path, action="append", default=[])
    parser.add_argument("--archive-dir", type=Path)
    parser.add_argument("--output", type=Path, default=Path("public/data/attractions.json"))
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    archives = [*args.archive, *(sorted(args.archive_dir.glob("amap-*.json")) if args.archive_dir else [])]
    if not archives:
        parser.error("至少需要一份坐标原始响应存档")
    try:
        publish(args.source, archives, args.output, args.report)
        return 0
    except (DataError, OSError, ValueError) as error:
        print(f"增补未发布：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
