import type {
  Attraction,
  AttractionDataset,
  AttractionFilters,
  BoundaryLevel,
  RegionContext,
  RegionTrailItem,
} from "../types";
import { nearbyRegionBounds } from "../map/geometry";

const DATASET_PATH = import.meta.env?.VITE_ATTRACTION_DATA_SOURCE === "static"
  ? "./data/attractions.json" : "/api/attractions";

function isValidLevel(level: number): boolean {
  return Number.isInteger(level) && level >= 0 && level <= 9;
}

function isValidCoordinate(value: number): boolean {
  return Number.isFinite(value);
}

export function isValidAttraction(item: Attraction): boolean {
  return Boolean(
    item.id &&
      item.name &&
      /^\d{6}$/.test(item.adcode) &&
      isValidLevel(item.level) &&
      isValidCoordinate(item.lng) &&
      isValidCoordinate(item.lat),
  );
}

export function belongsToRegion(item: Attraction, region: RegionContext): boolean {
  if (region.level === "country") {
    return true;
  }

  if (region.level === "province" || region.adcode.endsWith("0000")) {
    return item.adcode.slice(0, 2) === region.adcode.slice(0, 2);
  }

  if (region.level === "city" && region.adcode.endsWith("00")) {
    return item.adcode.slice(0, 4) === region.adcode.slice(0, 4);
  }

  return item.adcode === region.adcode;
}

export function visibleAttractions(
  items: Attraction[],
  region: RegionContext,
): Attraction[] {
  return items
    .filter((item) => belongsToRegion(item, region))
    .sort((left, right) => right.level - left.level || left.name.localeCompare(right.name, "zh-CN"));
}

export function attractionLabelScope(items: Attraction[], region: RegionTrailItem | null): {
  primaryIds: string[];
  nearbyIds: string[];
} {
  if (!region || region.level === "country") return { primaryIds: items.map((item) => item.id), nearbyIds: [] };
  const [west, south, east, north] = nearbyRegionBounds(region.bounds);
  const primaryIds: string[] = [];
  const nearbyIds: string[] = [];
  for (const item of items) {
    if (belongsToRegion(item, region)) primaryIds.push(item.id);
    else if (item.lng >= west && item.lng <= east && item.lat >= south && item.lat <= north) nearbyIds.push(item.id);
  }
  return { primaryIds, nearbyIds };
}

export function filterAttractions(items: Attraction[], filters: AttractionFilters): Attraction[] {
  const words = filters.query.normalize("NFKC").trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  return items.filter((item) => {
    if (filters.levels !== null && !filters.levels.includes(item.level)) return false;
    const text = [item.name, item.province, item.city, item.district, item.address]
      .join(" ").normalize("NFKC").toLocaleLowerCase();
    return words.every((word) => text.includes(word));
  });
}

export function boundaryLevelLabel(level: BoundaryLevel | "country"): string {
  return {
    country: "全国范围",
    province: "已选省级区域",
    city: "已选城市",
    district: "已选区县",
  }[level];
}

export class AttractionStore {
  private dataset: AttractionDataset | null = null;

  async load(): Promise<AttractionDataset> {
    if (this.dataset) {
      return this.dataset;
    }

    const response = await fetch(DATASET_PATH, { signal: AbortSignal.timeout(15_000) });
    if (!response.ok) {
      throw new Error(`景点数据加载失败（${response.status}）`);
    }

    const dataset = (await response.json()) as AttractionDataset;
    if (dataset.schemaVersion !== 1 || dataset.coordSystem !== "GCJ-02") {
      throw new Error("景点数据版本或坐标系不受支持");
    }

    const invalidCount = dataset.items.filter((item) => !isValidAttraction(item)).length;
    if (invalidCount > 0) {
      throw new Error(`景点数据包含 ${invalidCount} 条无效记录`);
    }

    this.dataset = dataset;
    return dataset;
  }
}
