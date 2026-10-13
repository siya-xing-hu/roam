import { createHash } from "node:crypto";
import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import type { AttractionDataset, BoundaryCollection, BoundaryFeature, MapBounds } from "../../src/types";
import { featureBounds } from "../../src/map/geometry";
import { isValidAttraction } from "../../src/data/attractionStore";
import { migrationPool, reportDatabaseError, root } from "./environment";

interface Region {
  adcode: string; name: string; level: string; parent: string | null; children: number;
  bounds: MapBounds; geometry: BoundaryFeature["geometry"] | null;
  properties: BoundaryFeature["properties"]; sourcePath: string;
}

async function boundaryFiles(directory: string): Promise<string[]> {
  const result: string[] = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const file = path.join(directory, entry.name);
    if (entry.isDirectory()) result.push(...await boundaryFiles(file));
    else if (file.endsWith(".geojson")) result.push(file);
  }
  return result.sort();
}

const pool = migrationPool();
try {
  const datasetPath = path.join(root, "public/data/attractions.json");
  const contents = await readFile(datasetPath, "utf8");
  const dataset = JSON.parse(contents) as AttractionDataset;
  if (dataset.schemaVersion !== 1 || dataset.coordSystem !== "GCJ-02" || !dataset.items.length) {
    throw new Error("景点数据格式或坐标系不受支持");
  }
  const ids = new Set<string>();
  for (const item of dataset.items) {
    if (!isValidAttraction(item) || ids.has(item.id) || Math.abs(item.lng) > 180 || Math.abs(item.lat) > 90) {
      throw new Error(`重复或无效景点：${item.id}`);
    }
    ids.add(item.id);
  }
  const regions = new Map<string, Region>();
  const boundaryDirectory = path.join(root, "public/data/boundaries");
  const indexContents = await readFile(path.join(boundaryDirectory, "index.json"), "utf8");
  const boundaryMetadata = JSON.parse(indexContents) as Record<string, unknown>;
  if (boundaryMetadata.coordSystem !== "GCJ-02") throw new Error("行政边界坐标系不受支持");
  const hash = createHash("sha256").update(contents).update(indexContents);
  for (const file of await boundaryFiles(boundaryDirectory)) {
    const raw = await readFile(file, "utf8");
    hash.update(path.relative(boundaryDirectory, file)).update(raw);
    const collection = JSON.parse(raw) as BoundaryCollection;
    for (const feature of collection.features) {
      const properties = feature.properties;
      const adcode = String(properties.adcode);
      if (!/^\d{6}$/.test(adcode) || !properties.name || !properties.level) continue;
      if (regions.has(adcode)) throw new Error(`重复区域：${adcode}`);
      const bounds = featureBounds(feature);
      if (!bounds.every(Number.isFinite)) throw new Error(`无效区域范围：${adcode}`);
      regions.set(adcode, {
        adcode, name: properties.name, level: properties.level,
        parent: properties.parent?.adcode ? String(properties.parent.adcode) : "100000",
        children: properties.childrenNum ?? 0, bounds, geometry: feature.geometry, properties,
        sourcePath: path.relative(path.join(root, "public/data"), file),
      });
    }
  }
  const bounds = [...regions.values()].reduce<MapBounds>((acc, region) => [
    Math.min(acc[0], region.bounds[0]), Math.min(acc[1], region.bounds[1]),
    Math.max(acc[2], region.bounds[2]), Math.max(acc[3], region.bounds[3]),
  ], [Infinity, Infinity, -Infinity, -Infinity]);
  regions.set("100000", {
    adcode: "100000", name: "全国", level: "country", parent: null,
    children: [...regions.values()].filter((region) => region.level === "province").length,
    bounds, geometry: null, properties: { adcode: "100000", name: "全国" }, sourcePath: "boundaries/index.json",
  });
  for (const region of regions.values()) {
    if (region.parent && !regions.has(region.parent)) throw new Error(`缺少上级区域：${region.adcode}`);
  }
  const sourceHash = hash.digest("hex");
  const { items, ...metadata } = dataset;
  const client = await pool.connect();
  let ancestorCount = 0;
  let unmatchedCount = 0;
  try {
    await client.query("BEGIN");
    await client.query("SELECT pg_advisory_xact_lock(726626, 1)");
    for (const region of regions.values()) {
      await client.query(`INSERT INTO roam.regions
        (adcode, name, level, parent_adcode, children_count, bounds, geometry, properties, source_path)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
        ON CONFLICT (adcode) DO UPDATE SET name=EXCLUDED.name, level=EXCLUDED.level,
        parent_adcode=EXCLUDED.parent_adcode, children_count=EXCLUDED.children_count, bounds=EXCLUDED.bounds,
        geometry=EXCLUDED.geometry, properties=EXCLUDED.properties, source_path=EXCLUDED.source_path, updated_at=now()`,
      [region.adcode, region.name, region.level, region.parent, region.children,
        region.bounds, region.geometry, region.properties, region.sourcePath]);
    }
    for (const [ordinal, item] of items.entries()) {
      const regionCode = [item.adcode, item.adcode.slice(0, 4) + "00", item.adcode.slice(0, 2) + "0000"]
        .find((code) => regions.has(code)) ?? null;
      const resolution = regionCode === item.adcode ? "exact" : regionCode ? "ancestor" : "unmatched";
      if (resolution === "ancestor") ancestorCount++;
      if (resolution === "unmatched") unmatchedCount++;
      await client.query(`INSERT INTO roam.attractions
        (id,name,province,city,district,adcode,region_adcode,region_resolution,address,lng,lat,level,level_source,tags,source,coordinate_source,ordinal)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
        ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name, province=EXCLUDED.province, city=EXCLUDED.city,
        district=EXCLUDED.district, adcode=EXCLUDED.adcode, region_adcode=EXCLUDED.region_adcode,
        region_resolution=EXCLUDED.region_resolution, address=EXCLUDED.address, lng=EXCLUDED.lng, lat=EXCLUDED.lat,
        level=EXCLUDED.level, level_source=EXCLUDED.level_source, tags=EXCLUDED.tags, source=EXCLUDED.source,
        coordinate_source=EXCLUDED.coordinate_source, ordinal=EXCLUDED.ordinal, updated_at=now()`,
      [item.id, item.name, item.province, item.city, item.district, item.adcode, regionCode, resolution,
        item.address, item.lng, item.lat, item.level, item.levelSource, item.tags, item.source, item.coordinateSource ?? null, ordinal]);
    }
    for (const [key, value] of [["attractions", metadata], ["boundaries", boundaryMetadata]]) {
      await client.query(`INSERT INTO roam.datasets(key,metadata,source_sha256) VALUES ($1,$2,$3)
        ON CONFLICT (key) DO UPDATE SET metadata=EXCLUDED.metadata, source_sha256=EXCLUDED.source_sha256, imported_at=now()`,
      [key, value, sourceHash]);
    }
    await client.query(`INSERT INTO roam.imports(source_sha256,metadata,attraction_count,region_count) VALUES ($1,$2,$3,$4)
      ON CONFLICT (source_sha256) DO UPDATE SET imported_at=now()`,
    [sourceHash, { attractions: metadata, boundaries: boundaryMetadata }, items.length, regions.size]);
    await client.query("COMMIT");
  } catch (error) {
    await client.query("ROLLBACK");
    throw error;
  } finally {
    client.release();
  }
  console.log(`已导入 ${items.length} 个景点、${regions.size - 1} 个行政区域和全国根节点。`);
  console.log(`区域匹配：${ancestorCount} 个景点回退上级区域，${unmatchedCount} 个未匹配；原始编码和坐标来源完整保留。`);
} catch (error) {
  reportDatabaseError(error);
} finally {
  await pool.end();
}
