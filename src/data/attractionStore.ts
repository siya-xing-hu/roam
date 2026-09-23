import { DISPLAY_THRESHOLD } from "../config";
import type {
  Attraction,
  AttractionDataset,
  BoundaryLevel,
  RegionContext,
} from "../types";

const DATASET_PATH = "./data/attractions.json";

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

  if (region.level === "city") {
    return item.adcode.slice(0, 4) === region.adcode.slice(0, 4);
  }

  return item.adcode === region.adcode;
}

export function visibleAttractions(
  items: Attraction[],
  region: RegionContext,
): Attraction[] {
  const threshold = DISPLAY_THRESHOLD[region.level];

  return items
    .filter((item) => belongsToRegion(item, region))
    .filter((item) => item.level >= threshold || item.level >= 6)
    .sort((left, right) => right.level - left.level || left.name.localeCompare(right.name, "zh-CN"));
}

export function boundaryLevelLabel(level: BoundaryLevel | "country"): string {
  return {
    country: "省级边界",
    province: "市级边界",
    city: "区县边界",
    district: "区县视图",
  }[level];
}

export class AttractionStore {
  private dataset: AttractionDataset | null = null;

  async load(): Promise<AttractionDataset> {
    if (this.dataset) {
      return this.dataset;
    }

    const response = await fetch(DATASET_PATH);
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
