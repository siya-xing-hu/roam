#!/usr/bin/env python3
"""Archive Guangxi's original coordinates; never guess an undeclared CRS.

The public detail page plots the values using T.Map, but the dataset does not
declare a coordinate system. Alternative transforms are diagnostic only.
"""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

from coordinate_math import bd09_to_gcj02, wgs84_to_gcj02
from geocode_amap import administrative_context, atomic_write_json, iso_timestamp, name_key
from offline_coordinates import AdminLocator, file_hash, object_names
from publish_coordinates import distance_m


def audit(raw_path: Path, osm_path: Path, output: Path):
    raw = json.loads(raw_path.read_text())["data"]
    osm = json.loads(osm_path.read_text())
    locator, index, locations = AdminLocator(), defaultdict(dict), Counter()
    for candidate in osm["items"]:
        if candidate["province"] != "广西壮族自治区":
            continue
        for name in object_names(candidate["tags"]):
            key = name_key(name, candidate, preserve_kind=True)
            index[candidate["adcode"], key][candidate["externalId"]] = candidate
    rows = []
    for row in raw:
        entry = {"externalId": row["rec_id"], "name": row["f_2023817820739"],
                 "regionText": row["f_2023818369421"], "url": row["doc_pub_url"],
                 "originalCoordinate": row.get("f_2023818961225", ""), "coordSystem": "unspecified",
                 "status": "needs_crs_confirmation", "hypotheses": []}
        try:
            lng, lat = map(float, entry["originalCoordinate"].split(","))
            if not (70 <= lng <= 140 and 0 <= lat <= 60):
                raise ValueError("outside extent")
        except (ValueError, AttributeError):
            entry["status"] = "missing_or_invalid_coordinate"
            rows.append(entry)
            continue
        locations[entry["originalCoordinate"]] += 1
        context = administrative_context({"id": row["rec_id"], "name": entry["name"],
                                          "regionText": "广西壮族自治区" + entry["regionText"],
                                          "provinceHints": ["广西壮族自治区"]})
        key = name_key(entry["name"], context, preserve_kind=True)
        for system, coords in (("WGS84", wgs84_to_gcj02(lng, lat)), ("GCJ-02", (lng, lat)), ("BD-09", bd09_to_gcj02(lng, lat))):
            admin = locator.locate(*coords)
            result = {"assumedSystem": system, "gcj02": coords, "admin": admin}
            if admin:
                candidates = list(index.get((admin["adcode"], key), {}).values())
                if len(candidates) == 1:
                    candidate = candidates[0]
                    other = list(map(float, candidate["location"].split(",")))
                    result["comparison"] = {"osmUrl": candidate["url"], "distanceToRepresentativeM": round(distance_m(
                        {"lng": coords[0], "lat": coords[1]}, {"lng": other[0], "lat": other[1]}))}
            entry["hypotheses"].append(result)
        if all(not value["admin"] for value in entry["hypotheses"]):
            entry["status"] = "outside_known_boundaries"
        elif all(not value["admin"] or value["admin"]["province"] != "广西壮族自治区" for value in entry["hypotheses"]):
            entry["status"] = "possible_region_mismatch"
        rows.append(entry)
    for entry in rows:
        entry["sharedCoordinateCount"] = locations[entry["originalCoordinate"]]
    result = {"schemaVersion": 1, "stage": "official-coordinate-audit", "generatedAt": iso_timestamp(),
              "source": {"file": str(raw_path), "sha256": file_hash(raw_path),
                         "url": "http://wlt.gxzf.gov.cn/zfxxgk/fdzdgknr/bmcxfw/lyjdjqcx/"},
              "note": "坐标系未声明，转换假设只供核对；代表点可能不同，距离不能单独证明坐标系。此文件不用于自动发布。",
              "summary": {"total": len(rows), "statuses": dict(Counter(entry["status"] for entry in rows)),
                          "sharedCoordinateRows": sum(entry["sharedCoordinateCount"] > 1 for entry in rows)},
              "items": rows}
    atomic_write_json(output, result)
    print(json.dumps(result["summary"], ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--osm-candidates", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if len({path.resolve() for path in (args.raw, args.osm_candidates, args.output)}) != 3:
        parser.error("Inputs and output must use different paths")
    audit(args.raw, args.osm_candidates, args.output)
