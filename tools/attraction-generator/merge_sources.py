#!/usr/bin/env python3
"""合并本地景点基础资料，保留身份和来源，另存待确认结果；不访问网络。"""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from difflib import SequenceMatcher
from pathlib import Path
import re
import sys
import unicodedata

from fetch_mct import atomic_write_json, iso_timestamp
from geocode_amap import DataError, administrative_context, read_source

ROOT = Path(__file__).resolve().parent
DEFAULT_INPUTS = [ROOT / "tmp/01-文旅部景点数据.json", ROOT / "tmp/01-地方文旅景点数据.json"]
DEFAULT_OUTPUT = ROOT / "tmp/01-汇总景点数据（待确认）.json"


def name_key(item: dict, context: dict) -> str:
    # 合并比 POI 搜索更严格，括号中的分馆、园区名称必须保留。
    name = re.sub(r"\s+", "", unicodedata.normalize("NFKC", item["name"]))
    prefixes = set(context.values()) - {""}
    prefixes.update(value[:-1] for value in tuple(prefixes) if value.endswith(("省", "市", "县")) and len(value) > 2)
    while True:
        prefix = next((value for value in sorted(prefixes, key=len, reverse=True)
                       if name.startswith(value) and len(name) - len(value) >= 2), None)
        if prefix is None:
            return name
        name = name[len(prefix):]


def add_unique(target: dict, incoming: dict, field: str) -> None:
    for value in incoming.get(field, []):
        if value not in target.setdefault(field, []):
            target[field].append(deepcopy(value))


def needs_review(item: dict, reason: str) -> None:
    reasons = [part for part in item.get("reviewReason", "").split("；") if part]
    for part in reason.split("；"):
        if part and part not in reasons:
            reasons.append(part)
    item.update(reviewStatus="needs_review", reviewReason="；".join(reasons))


def a_levels(item: dict) -> set[str]:
    return {value["value"] for value in item.get("classifications", []) if value["type"] == "a-level"}


def merge(inputs: list[Path], output: Path) -> int:
    resolved = [path.resolve() for path in inputs]
    if len(set(resolved)) != len(resolved):
        raise DataError("同一个输入文件不能重复指定")
    if output.resolve() in resolved:
        raise DataError("汇总结果必须另存，不能覆盖来源文件")
    documents = [read_source(path) for path in inputs]
    items: list[dict] = []
    contexts: list[dict] = []
    owners: list[int] = []
    combined = {"sources": [], "sourceRecords": []}
    merge_log, review_pairs = [], []
    for document_index, document in enumerate(documents):
        for field in ("sources", "sourceRecords"):
            add_unique(combined, document, field)
        for original in document["items"]:
            item = deepcopy(original)
            context = administrative_context(item)
            key = name_key(item, context)
            exact = []
            possible = []
            for index, (existing, previous) in enumerate(zip(items, contexts)):
                if existing["id"] == item["id"]:
                    if existing["name"] != item["name"] or existing["regionText"] != item["regionText"]:
                        raise DataError(f"同一 ID 对应不同地点：{item['id']}，需先核实")
                    exact.append(index)
                    continue
                if owners[index] == document_index or previous["province"] != context["province"]:
                    continue
                if previous["city"] and context["city"] and previous["city"] != context["city"]:
                    continue
                if previous["district"] and context["district"] and previous["district"] != context["district"]:
                    continue
                previous_key = name_key(existing, previous)
                # 名称和完整行政区域均一致才自动合并；缺少区县的同名记录仍需核实。
                if previous_key == key and all(context.values()) and previous == context:
                    exact.append(index)
                elif min(len(key), len(previous_key)) >= 4 and (
                    key in previous_key or previous_key in key
                    or SequenceMatcher(None, key, previous_key).ratio() >= 0.88
                ):
                    possible.append(index)
            if len(exact) == 1:
                target = items[exact[0]]
                if a_levels(target) and a_levels(item) and a_levels(target) != a_levels(item):
                    needs_review(target, f"来源 A 级不一致：{target['name']}，需确认当前等级")
                for field in ("classifications", "sources", "sourceRecordIds", "aliases", "nameEvidence", "selectionEvidence"):
                    add_unique(target, item, field)
                if item["reviewStatus"] == "needs_review":
                    needs_review(target, item.get("reviewReason") or "新增来源需核实")
                if item["reviewStatus"] == "skip" and target["reviewStatus"] != "skip":
                    needs_review(target, "新增来源标记跳过：" + item["skipReason"])
                merge_log.append({"keptId": target["id"], "mergedId": item["id"], "name": target["name"]})
                continue
            if len(exact) > 1:
                possible.extend(exact)
            if possible and item["reviewStatus"] != "skip":
                item["possibleDuplicateIds"] = list(dict.fromkeys(items[index]["id"] for index in possible))
                needs_review(item, "与已有景点名称相近，需核实是否同一地点或独立组成部分")
                for index in possible:
                    review_pairs.append({"existingId": items[index]["id"], "existingName": items[index]["name"],
                                         "incomingId": item["id"], "incomingName": item["name"]})
            items.append(item)
            contexts.append(context)
            owners.append(document_index)
    if len({item["id"] for item in items}) != len(items):
        raise DataError("汇总后 ID 重复，停止发布；请核实同名地点的身份")
    summary = {"total": len(items), "inputTotal": sum(len(document["items"]) for document in documents),
               "merged": len(merge_log), "ready": sum(item["reviewStatus"] == "ready" for item in items),
               "needsReview": sum(item["reviewStatus"] == "needs_review" for item in items),
               "skipped": sum(item["reviewStatus"] == "skip" for item in items),
               "levels": dict(Counter(max(a_levels(item), default="未评级") for item in items))}
    result = {
        "schemaVersion": 1, "stage": "attraction-source", "generatedAt": iso_timestamp(),
        "summary": summary,
        "inputFiles": [{"path": str(path), "generatedAt": document.get("generatedAt"),
                        "rawArchive": document.get("rawArchive"),
                        "coverageNote": document.get("coverageNote", ""), "itemCount": len(document["items"])}
                       for path, document in zip(inputs, documents)],
        "coverageNote": "各输入只代表其声明的来源覆盖范围，不代表全国各等级景区全量。",
        **combined, "mergeLog": merge_log, "reviewPairs": review_pairs, "items": items,
    }
    atomic_write_json(output, result)
    print(f"已汇总 {summary['total']} 条：合并 {summary['merged']} 条，需核实 {summary['needsReview']} 条。\n{output}")
    for item in items:
        if item["reviewStatus"] == "needs_review":
            print(f"待核实：{item['name']}（{item['regionText']}）：{item['reviewReason']}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, nargs="+", default=DEFAULT_INPUTS, help="基础资料文件，已有主数据放最前，保留其 ID")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="另存的待确认基础资料")
    args = parser.parse_args()
    try:
        return merge(args.input, args.output)
    except (DataError, ValueError, OSError, KeyError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
