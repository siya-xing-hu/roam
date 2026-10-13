#!/usr/bin/env python3
"""Incrementally add verified OSM coordinates without changing existing map points."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path

from build_map_data import map_item
from coordinate_math import wgs84_to_gcj02
from geocode_amap import DataError, atomic_write_json, iso_timestamp, name_key, read_source, source_fingerprint
from offline_coordinates import AdminLocator, eligible, file_hash, identity_matches


def distance_m(a: dict, b: dict) -> float:
    lat1, lat2 = math.radians(a["lat"]), math.radians(b["lat"])
    delta = math.radians(b["lng"] - a["lng"])
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(delta / 2) ** 2
    return 12742000 * math.asin(min(1, math.sqrt(h)))


def validate_candidate(item: dict, binding: dict, locator: AdminLocator) -> dict:
    if item["reviewStatus"] != "ready" or binding.get("sourceFingerprint") != source_fingerprint(item):
        raise DataError("Source changed or requires review; rerun matching")
    candidate = binding["selected"]
    if not eligible(candidate["tags"]) or not identity_matches(item, candidate):
        raise DataError("OSM name/type/admin evidence does not match source")
    original = candidate["original"]
    if original.get("coordSystem") != "WGS84" or not all(math.isfinite(original[key]) for key in ("lng", "lat")):
        raise DataError("OSM must retain finite original WGS84 coordinates")
    if not (70 <= original["lng"] <= 140 and 0 <= original["lat"] <= 60):
        raise DataError("Coordinates outside expected extent")
    lng, lat = wgs84_to_gcj02(original["lng"], original["lat"])
    admin = locator.locate(lng, lat)
    if not admin or any(candidate[key] != admin[key] for key in ("province", "city", "district", "adcode")):
        raise DataError("Point is outside its recorded administrative area")
    if candidate["method"] == "point-on-surface":
        from shapely import from_wkb
        from shapely.geometry import Point
        geometry = from_wkb(candidate["geometryWkb"])
        if not geometry.is_valid or not geometry.covers(Point(original["lng"], original["lat"])):
            raise DataError("Representative point is outside OSM geometry")
    elif candidate["method"] != "mapped-point":
        raise DataError("Unknown coordinate method")
    return {**candidate, "location": f"{lng:.6f},{lat:.6f}"}


def publish(source_path: Path, archive_path: Path, output: Path, report_path: Path) -> dict:
    if len({path.resolve() for path in (source_path, archive_path, output, report_path)}) != 4:
        raise DataError("Inputs, map and report must use different paths")
    before_bytes = output.read_bytes()
    before = json.loads(before_bytes)
    if before.get("schemaVersion") != 1 or before.get("coordSystem") != "GCJ-02":
        raise DataError("Unsupported map format")
    source = read_source(source_path)
    archive = json.loads(archive_path.read_text())
    if archive.get("stage") != "coordinate-bindings" or archive.get("provider") != "OpenStreetMap":
        raise DataError("Unsupported coordinate provider/archive")
    candidate_path = Path(archive["candidateArchive"])
    if file_hash(candidate_path) != archive["candidateSha256"]:
        raise DataError("Candidate archive changed; rerun matching")
    candidates = {candidate["externalId"]: candidate for candidate in json.loads(candidate_path.read_text())["items"]}
    bindings = {binding["id"]: binding for binding in archive["items"]}
    original = {item["id"]: item for item in before["items"]}
    if len(original) != len(before["items"]) or len(bindings) != len(archive["items"]):
        raise DataError("Duplicate map/binding IDs")
    result, owners, name_index = dict(original), {}, defaultdict(list)
    for item in original.values():
        coordinate_source = item.get("coordinateSource", {})
        if coordinate_source.get("provider") == "OpenStreetMap":
            owners[coordinate_source["externalId"]] = item["id"]
        key = name_key(item["name"], item, preserve_kind=True)
        name_index[item["adcode"], key].append(item)
    # A point already located by Amap can also own an OSM object. Reserve those
    # associations before adding new catalog IDs, including different grades.
    for item_id, binding in bindings.items():
        if item_id in original and binding.get("status") == "matched":
            selected = binding["selected"]
            if candidates.get(selected["externalId"]) == selected:
                owners.setdefault(selected["externalId"], item_id)
    added, pending = [], []
    locator = AdminLocator()
    for item in source["items"]:
        if item["id"] in original or item["reviewStatus"] == "skip":
            continue
        binding = bindings.get(item["id"], {})
        status = binding.get("status", item["reviewStatus"])
        reason = item.get("reviewReason", "") or {
            "not_found": "No OSM object meets name, type and administrative checks",
            "ambiguous": "Multiple distinct OSM candidates require review",
        }.get(status, "")
        if status == "matched":
            try:
                selected = binding["selected"]
                if candidates.get(selected["externalId"]) != selected:
                    raise DataError("Selected object differs from raw candidate archive")
                selected = validate_candidate(item, binding, locator)
                selected = {**selected, "address": item.get("address") or selected["address"]}
                value = map_item(item, selected)
                owner = owners.get(selected["externalId"])
                key = name_key(value["name"], value, preserve_kind=True)
                nearby = [other for other in name_index[value["adcode"], key] if distance_m(value, other) < 500]
                if owner or nearby:
                    status, reason = "possible_duplicate", f"Possibly same place as {owner or nearby[0]['id']}"
                else:
                    value["coordinateSource"] = {
                        "provider": "OpenStreetMap", "name": "OpenStreetMap contributors", "url": selected["url"],
                        "externalId": selected["externalId"], "license": "ODbL-1.0",
                        "licenseUrl": "https://www.openstreetmap.org/copyright",
                        "original": selected["original"], "method": selected["method"],
                        "snapshotSha256": archive["source"]["sha256"],
                    }
                    result[item["id"]] = value
                    owners[selected["externalId"]] = item["id"]
                    name_index[value["adcode"], key].append(value)
                    added.append(item["id"])
                    continue
            except DataError as error:
                status, reason = "needs_review", str(error)
        pending.append({"id": item["id"], "name": item["name"], "regionText": item["regionText"],
                        "status": status, "reason": reason, "candidateIds": binding.get("candidateIds", []),
                        "sources": item["sources"], "classifications": item["classifications"]})
    items = sorted(result.values(), key=lambda value: (-value["level"], value["name"], value["id"]))
    now = iso_timestamp()
    dataset = {**before, "generatedAt": now, "coverage": sorted({value["province"] for value in items}), "items": items}
    summary = {"before": len(original), "after": len(items), "added": len(added), "updated": 0,
               "unresolved": len(pending), "statuses": dict(Counter(value["status"] for value in pending)),
               "levels": dict(Counter(value["level"] for value in items))}
    report = {"generatedAt": now, "source": str(source_path), "archive": str(archive_path),
              "summary": summary, "addedIds": added, "unresolved": pending}
    if output.read_bytes() != before_bytes:
        raise DataError("Map changed during publication; retry")
    atomic_write_json(report_path, report)
    # An idempotent run does not even change generatedAt.
    if added:
        atomic_write_json(output, dataset)
    print(json.dumps(summary, ensure_ascii=False))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--output", default=Path("public/data/attractions.json"), type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    publish(args.source, args.archive, args.output, args.report)


if __name__ == "__main__":
    main()
