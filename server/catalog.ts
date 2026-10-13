import type { Pool } from "pg";
import type { Attraction, AttractionDataset } from "../src/types";

export interface CatalogQuery {
  region?: string;
  bounds?: [number, number, number, number];
  limit?: number;
  minimumLevel?: number;
}

export class InvalidQuery extends Error {}

export function parseCatalogQuery(parameters: URLSearchParams): CatalogQuery {
  const query: CatalogQuery = {};
  const accepted = new Set(["region", "bbox", "limit", "minimumLevel"]);
  for (const key of parameters.keys()) {
    if (!accepted.has(key) || parameters.getAll(key).length !== 1) throw new InvalidQuery("查询参数不受支持或重复");
  }
  if (parameters.has("region")) {
    const region = parameters.get("region")!;
    if (!/^\d{6}$/.test(region)) throw new InvalidQuery("区域编码必须是 6 位数字");
    query.region = region;
  }
  if (parameters.has("bbox")) {
    const parts = parameters.get("bbox")!.split(",");
    const bounds = parts.map(Number);
    if (parts.length !== 4 || parts.some((part) => !part.trim()) || !bounds.every(Number.isFinite)
      || bounds[0] < -180 || bounds[2] > 180 || bounds[1] < -90 || bounds[3] > 90
      || bounds[0] >= bounds[2] || bounds[1] >= bounds[3]) throw new InvalidQuery("地图范围无效");
    query.bounds = bounds as CatalogQuery["bounds"];
  }
  for (const key of ["limit", "minimumLevel"] as const) {
    if (!parameters.has(key)) continue;
    const value = parameters.get(key)!;
    const number = Number(value);
    if (!/^\d+$/.test(value) || number < (key === "limit" ? 1 : 0)
      || number > (key === "limit" ? 1000 : 9)) throw new InvalidQuery(`${key} 超出允许范围`);
    query[key] = number;
  }
  return query;
}

interface AttractionRow {
  id: string; name: string; province: string; city: string; district: string;
  adcode: string; address: string; lng: number; lat: number; level: number;
  level_source: Attraction["levelSource"]; tags: string[];
  source: Attraction["source"]; coordinate_source: Attraction["coordinateSource"] | null;
}

export async function readCatalog(pool: Pool, query: CatalogQuery = {}): Promise<AttractionDataset> {
  const values: unknown[] = [];
  const parameter = (value: unknown) => { values.push(value); return `$${values.length}`; };
  const where: string[] = [];
  let prefix = "";
  if (query.region) {
    prefix = `WITH RECURSIVE scope AS (
      SELECT adcode FROM roam.regions WHERE adcode = ${parameter(query.region)}
      UNION SELECT child.adcode FROM roam.regions child JOIN scope parent ON child.parent_adcode = parent.adcode
    )`;
    where.push("region_adcode IN (SELECT adcode FROM scope)");
  }
  if (query.bounds) {
    const [west, south, east, north] = query.bounds;
    where.push(`lng BETWEEN ${parameter(west)} AND ${parameter(east)}`);
    where.push(`lat BETWEEN ${parameter(south)} AND ${parameter(north)}`);
  }
  if (query.minimumLevel !== undefined) where.push(`level >= ${parameter(query.minimumLevel)}`);
  const order = query.limit === undefined ? "ordinal, id" : "level DESC, id";
  const sql = `${prefix} SELECT id, name, province, city, district, adcode, address, lng, lat,
    level, level_source, tags, source, coordinate_source FROM roam.attractions
    ${where.length ? `WHERE ${where.join(" AND ")}` : ""} ORDER BY ${order}
    ${query.limit === undefined ? "" : `LIMIT ${parameter(query.limit)}`}`;
  const client = await pool.connect();
  try {
    await client.query("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY");
    const metadata = await client.query<{ metadata: Omit<AttractionDataset, "items"> }>(
      "SELECT metadata FROM roam.datasets WHERE key = 'attractions'",
    );
    if (!metadata.rows[0]) throw new Error("Catalog has not been imported");
    const result = await client.query<AttractionRow>(sql, values);
    await client.query("COMMIT");
    return {
      ...metadata.rows[0].metadata,
      items: result.rows.map(({ level_source, coordinate_source, ...row }) => ({
        ...row, levelSource: level_source,
        ...(coordinate_source ? { coordinateSource: coordinate_source } : {}),
      })),
    };
  } catch (error) {
    await client.query("ROLLBACK");
    throw error;
  } finally {
    client.release();
  }
}
