#!/usr/bin/env python3
"""Match official catalogs against an OSM PBF, retaining evidence and ambiguity.

Requires osmium and shapely. No online geocoder is called.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path
import re

from coordinate_math import wgs84_to_gcj02
from geocode_amap import (DataError, administrative_context, atomic_write_json,
                          iso_timestamp, lookup_item, name_key, read_source,
                          same_admin, source_fingerprint)

BOUNDARIES = Path(__file__).resolve().parents[2] / "public/data/boundaries"
NAME_FIELDS = ("name", "name:zh", "name:zh-Hans", "official_name", "official_name:zh", "alt_name", "alt_name:zh", "short_name")


def file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def eligible(tags: dict) -> bool:
    # Exclude entrances, transport, accommodation and facilities, even if named.
    if any(tags.get(key) for key in ("entrance", "highway", "railway", "public_transport")):
        return False
    if tags.get("tourism") in {"hotel", "hostel", "guest_house", "information", "camp_site"}:
        return False
    if tags.get("amenity") in {"parking", "toilets", "restaurant", "cafe", "bus_station"}:
        return False
    return bool(tags.get("tourism") in {"attraction", "museum", "zoo", "theme_park", "aquarium", "gallery", "viewpoint"}
                or tags.get("leisure") in {"park", "garden", "nature_reserve", "water_park"}
                or tags.get("historic") not in (None, "", "no")
                or tags.get("amenity") == "place_of_worship")


def object_names(tags: dict) -> list[str]:
    return sorted({part.strip() for key in NAME_FIELDS for part in tags.get(key, "").split(";")
                   if re.search(r"[\u4e00-\u9fff]", part)})


class AdminLocator:
    """Resolve GCJ-02 points using the same polygons displayed by the app."""
    def __init__(self, root: Path = BOUNDARIES):
        self.root = root

    @lru_cache(maxsize=512)
    def features(self, relative: str):
        from shapely.geometry import shape
        from shapely.prepared import prep
        path = self.root / relative
        if not path.exists():
            return []
        return [(feature["properties"], prep(shape(feature["geometry"])))
                for feature in json.loads(path.read_text())["features"]]

    def at(self, relative: str, point):
        matches = [props for props, geometry in self.features(relative) if geometry.covers(point)]
        return matches[0] if len(matches) == 1 else None

    def locate(self, lng: float, lat: float) -> dict | None:
        from shapely.geometry import Point
        point = Point(lng, lat)
        province = self.at("provinces.geojson", point)
        if not province:
            return None
        city_path = f"cities/{province['adcode']}.geojson"
        # Municipalities have districts directly under the province code.
        if not (self.root / city_path).exists():
            city_path = f"counties/{province['adcode']}.geojson"
        city = self.at(city_path, point)
        if not city:
            return None
        if city.get("level") == "district":
            return {"province": province["name"], "city": province["name"], "district": city["name"], "adcode": str(city["adcode"])}
        district = self.at(f"counties/{city['adcode']}.geojson", point)
        if not district:
            # Province-administered county cities already have a county adcode.
            if not str(city["adcode"]).endswith("00"):
                district = city
            else:
                return None
        return {"province": province["name"], "city": city["name"], "district": district["name"], "adcode": str(district["adcode"])}


def compatible_admin(expected: dict, actual: dict) -> bool:
    if expected["province"] != actual["province"]:
        return False
    # Some official lists put county-level cities into the city column.
    if expected["city"] and not any(same_admin(expected["city"], actual[key]) for key in ("city", "district")):
        return False
    return not expected["district"] or same_admin(expected["district"], actual["district"])


def identity_matches(item: dict, candidate: dict) -> bool:
    if not eligible(candidate["tags"]):
        return False
    expected = administrative_context(lookup_item(item))
    if not compatible_admin(expected, candidate):
        return False
    item = lookup_item(item)
    keys = {name_key(name, expected, preserve_kind=True) for name in [item["name"], *item.get("aliases", [])]}
    actual_keys = {name_key(name, expected, preserve_kind=True) for name in object_names(candidate["tags"])}
    # Short/generic names need manual confirmation even when geographically close.
    return any(len(key) >= 3 and key not in {"博物馆", "纪念馆", "古村落", "森林公园", "湿地公园", "文化公园"}
               for key in keys & actual_keys)


def extract(pbf: Path, output: Path, source_url: str, source: Path | None = None) -> dict:
    import osmium
    from shapely import from_wkb
    locator, factory = AdminLocator(), osmium.geom.WKBFactory()
    values, rejected = [], Counter()
    relevant = None
    if source:
        relevant = set()
        for item in read_source(source)["items"]:
            if item["reviewStatus"] != "ready":
                continue
            item = lookup_item(item)
            context = administrative_context(item)
            relevant.update(name_key(name, context, preserve_kind=True) for name in [item["name"], *item.get("aliases", [])])
    # Areas are assembled from ways/relations; never use a polygon vertex as its center.
    for obj in osmium.FileProcessor(pbf).with_areas().with_filter(osmium.filter.KeyFilter(*NAME_FIELDS)):
        if not (obj.is_node() or obj.is_area()):
            continue
        tags = dict(obj.tags)
        if not eligible(tags) or not object_names(tags):
            continue
        if relevant is not None:
            # Candidate names may include administrative prefixes; a suffix check
            # cheaply limits geometry work, before exact name/admin validation.
            names = [name_key(name, {"province": "", "city": "", "district": ""}, preserve_kind=True)
                     for name in object_names(tags)]
            if not any(name[start:] in relevant for name in names for start in range(max(0, len(name) - 2))):
                continue
        geometry_hex = None
        if obj.is_node():
            if not obj.location.valid():
                rejected["invalid_location"] += 1
                continue
            lng, lat = obj.location.lon, obj.location.lat
            kind, object_id, method = "node", obj.id, "mapped-point"
        else:
            try:
                geometry_hex = factory.create_multipolygon(obj)
                geometry = from_wkb(geometry_hex)
            except (RuntimeError, ValueError):
                rejected["invalid_geometry"] += 1
                continue
            if not geometry.is_valid or geometry.is_empty:
                rejected["invalid_geometry"] += 1
                continue
            point = geometry.representative_point()
            lng, lat = point.x, point.y
            kind, object_id = ("way" if obj.from_way() else "relation"), obj.orig_id()
            method = "point-on-surface"
        gcj = wgs84_to_gcj02(lng, lat)
        admin = locator.locate(*gcj)
        if not admin:
            rejected["outside_known_district"] += 1
            continue
        key = f"{kind}/{object_id}"
        values.append({"externalId": key, "url": f"https://www.openstreetmap.org/{key}",
                       "original": {"lng": lng, "lat": lat, "coordSystem": "WGS84"},
                       "location": ",".join(f"{n:.6f}" for n in gcj), **admin,
                       "address": tags.get("addr:full", ""), "method": method,
                       "tags": tags, "geometryWkb": geometry_hex})
        if len(values) % 5000 == 0:
            print(f"OSM named candidates: {len(values)}", flush=True)
    archive = {"schemaVersion": 1, "stage": "osm-candidates", "generatedAt": iso_timestamp(),
               "source": {"url": source_url, "sha256": file_hash(pbf), "file": str(pbf),
                          "license": "ODbL-1.0", "licenseUrl": "https://www.openstreetmap.org/copyright"},
               "summary": {"candidates": len(values), "rejected": dict(rejected)}, "items": values}
    if source:
        archive["catalogScope"] = {"file": str(source), "sha256": file_hash(source)}
    atomic_write_json(output, archive)
    print(json.dumps(archive["summary"], ensure_ascii=False), flush=True)
    return archive


def select_unique(candidates: list[dict]) -> dict | None:
    """Accept one object, or one area plus identically named POIs inside it."""
    from shapely import from_wkb
    from shapely.geometry import Point
    if len(candidates) == 1:
        return candidates[0]
    areas = [candidate for candidate in candidates if candidate.get("geometryWkb")]
    if len(areas) != 1:
        return None
    area = areas[0]
    shape = from_wkb(area["geometryWkb"])
    for candidate in candidates:
        if candidate is area:
            continue
        point = candidate["original"]
        if not shape.covers(Point(point["lng"], point["lat"])):
            return None
    return area


def match(source: Path, candidates: Path, output: Path) -> dict:
    source_data = read_source(source)
    archive = json.loads(candidates.read_text())
    if archive.get("stage") != "osm-candidates" or archive.get("schemaVersion") != 1:
        raise DataError("Unsupported OSM candidate archive")
    index = defaultdict(dict)
    for candidate in archive["items"]:
        for name in object_names(candidate["tags"]):
            key = name_key(name, candidate, preserve_kind=True)
            index[candidate["province"], key][candidate["externalId"]] = candidate
    bindings = []
    for item in source_data["items"]:
        if item["reviewStatus"] != "ready":
            continue
        context = administrative_context(lookup_item(item))
        lookup = lookup_item(item)
        found = {}
        for name in [lookup["name"], *lookup.get("aliases", [])]:
            key = name_key(name, context, preserve_kind=True)
            found.update(index.get((context["province"], key), {}))
        valid = [candidate for candidate in found.values() if identity_matches(item, candidate)]
        selected = select_unique(valid)
        bindings.append({"id": item["id"], "name": item["name"], "sourceFingerprint": source_fingerprint(item),
                         "status": "matched" if selected else "ambiguous" if valid else "not_found",
                         "candidateIds": [value["externalId"] for value in valid], "selected": selected})
    report = {"schemaVersion": 1, "stage": "coordinate-bindings", "provider": "OpenStreetMap",
              "generatedAt": iso_timestamp(), "source": archive["source"],
              "candidateArchive": str(candidates), "candidateSha256": file_hash(candidates),
              "summary": dict(Counter(binding["status"] for binding in bindings)), "items": bindings}
    atomic_write_json(output, report)
    print(json.dumps(report["summary"], ensure_ascii=False), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    collect = actions.add_parser("extract")
    collect.add_argument("--pbf", type=Path, required=True)
    collect.add_argument("--source-url", required=True)
    collect.add_argument("--output", type=Path, required=True)
    collect.add_argument("--source", type=Path, help="Optionally limit extraction to catalog name candidates")
    matching = actions.add_parser("match")
    matching.add_argument("--source", type=Path, required=True)
    matching.add_argument("--candidates", type=Path, required=True)
    matching.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "extract":
        if args.pbf.resolve() == args.output.resolve():
            parser.error("Output must differ from PBF input")
        if args.source and args.source.resolve() == args.output.resolve():
            parser.error("Output must differ from source catalog")
        extract(args.pbf, args.output, args.source_url, args.source)
    else:
        if len({args.source.resolve(), args.candidates.resolve(), args.output.resolve()}) != 3:
            parser.error("Inputs and output must use different paths")
        match(args.source, args.candidates, args.output)


if __name__ == "__main__":
    main()
