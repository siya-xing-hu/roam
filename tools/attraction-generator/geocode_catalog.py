#!/usr/bin/env python3
"""对大名录分轮查询坐标，按省份轮转，并把原始请求分片保存。"""

from __future__ import annotations

import argparse
from collections import defaultdict, deque
from pathlib import Path
import os
import sys
import time

from geocode_amap import (
    DataError, POI_ENDPOINT, RETRY_BACKOFF_SECONDS, REUSABLE_OUTCOMES,
    atomic_write_json, classify_response, collector_lock, iso_timestamp,
    matching_state, new_archive, read_archive, read_source, request_once,
    merge_reusable_requests, save_archive,
)


def round_robin(items: list[dict]) -> list[dict]:
    groups = defaultdict(deque)
    for item in sorted(items, key=lambda i: (-max((int(c["value"][0]) for c in i["classifications"]
                                                  if c["type"] == "a-level"), default=0), i["id"])):
        groups[item["provinceHints"][0]].append(item)
    result = []
    while any(groups.values()):
        for province in sorted(groups):
            if groups[province]:
                result.append(groups[province].popleft())
    return result


def collect(input_path: Path, archive_dir: Path, reuse: list[Path], passes: int) -> int:
    source = read_source(input_path)
    archive_dir.mkdir(parents=True, exist_ok=True)
    with collector_lock():
        available = {}
        shards = sorted(archive_dir.glob("amap-*.json"))
        for path in [*reuse, *shards]:
            archive = read_archive(path)
            merge_reusable_requests(available, archive)
        history_index = defaultdict(set)

        def index_record(record: dict) -> None:
            for attraction_id in record.get("attractionIds", []):
                history_index[attraction_id].add(record["queryFingerprint"])

        for record in available.values():
            index_record(record)

        def match(item: dict) -> dict:
            keys = set().union(*(history_index.get(key, set()) for key in
                                 [item["id"], *item.get("sourceRecordIds", [])]))
            return matching_state(item, available, history=[available[key] for key in sorted(keys)])
        key = os.environ.get("AMAP_WEB_SERVICE_KEY", "").strip()
        if not key:
            raise DataError("缺少 AMAP_WEB_SERVICE_KEY")
        items = round_robin([item for item in source["items"] if item["reviewStatus"] == "ready"])
        shard_number = max((int(path.stem.split("-")[-1]) for path in shards), default=0) + 1
        shard = new_archive()
        timing = {"lastStarted": time.monotonic()}
        requests = 0
        aborted = False

        def report() -> dict:
            bindings = [match(item) for item in source["items"]]
            counts = {status: sum(b["matchStatus"] == status for b in bindings)
                      for status in ("matched", "pending", "needs_review", "skip")}
            atomic_write_json(archive_dir / "progress.json", {
                "generatedAt": iso_timestamp(), "input": str(input_path), "summary": counts,
                "requestsThisRun": requests, "aborted": aborted, "items": bindings,
            })
            print(f"进度：{counts}；本轮请求 {requests}", flush=True)
            return counts

        try:
            for pass_index in range(passes):
                pending = [item for item in items if match(item)["matchStatus"] == "pending"]
                if not pending:
                    break
                print(f"第 {pass_index + 1} 轮：{len(pending)} 个景点", flush=True)
                for index, item in enumerate(pending, 1):
                    binding = match(item)
                    if binding["matchStatus"] != "pending" or not binding["nextRequests"]:
                        continue
                    if len(shard["requests"]) >= 80:
                        shard_number += 1
                        shard = new_archive()
                    output = archive_dir / f"amap-{shard_number:05}.json"
                    # 地方景区包含大量公园、博物馆等，优先使用现有的综合景点查询。
                    # 只调整查询顺序，候选身份、地区、分页完整性等核验规则保持一致。
                    params = next((params for params in binding["nextRequests"]
                                   if params.get("types") == "风景名胜|科教文化服务|体育休闲服务"),
                                  binding["nextRequests"][0])
                    for attempt in range(len(RETRY_BACKOFF_SECONDS) + 1):
                        record = request_once(endpoint=POI_ENDPOINT, params=params, attraction_ids=[item["id"]],
                                              attempt=attempt + 1, api_key=key, archive=shard,
                                              output_path=output, timing=timing)
                        requests += 1
                        outcome, message = classify_response(record)
                        record.update(outcome=outcome, message=message)
                        save_archive(output, shard)
                        if outcome in REUSABLE_OUTCOMES:
                            available[record["queryFingerprint"]] = record
                            index_record(record)
                            break
                        if outcome == "fatal_error" or attempt == len(RETRY_BACKOFF_SECONDS):
                            print(f"采集停止：{message}", file=sys.stderr, flush=True)
                            aborted = True
                            break
                        time.sleep(RETRY_BACKOFF_SECONDS[attempt])
                    if index % 25 == 0 or aborted:
                        print(f"[{index}/{len(pending)}] {item['name']}", flush=True)
                    if index % 100 == 0:
                        report()
                    if aborted:
                        break
                report()
                if aborted:
                    break
        except KeyboardInterrupt:
            aborted = True
            print("采集已中断，已保存的请求可续接。", file=sys.stderr, flush=True)
        finally:
            counts = report()
        return int(aborted or counts["pending"] > 0 or counts["needs_review"] > 0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--archive-dir", type=Path, required=True)
    parser.add_argument("--reuse", type=Path, action="append", default=[])
    parser.add_argument("--passes", type=int, default=1)
    args = parser.parse_args()
    if args.passes < 1:
        parser.error("--passes 必须大于 0")
    try:
        return collect(args.input, args.archive_dir, args.reuse, args.passes)
    except (DataError, OSError) as error:
        print(f"采集未完成：{error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
