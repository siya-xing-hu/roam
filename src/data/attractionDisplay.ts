import { belongsToRegion } from "./attractionStore";
import { containsPoint } from "../map/viewportSelection";
import type { Attraction, MapBounds, RegionRecord } from "../types";

export interface AttractionDisplay {
  primaryIds: string[];
  nearbyNamedIds: string[];
  nearbyUnnamedIds: string[];
}

const rank = (a: Attraction, b: Attraction) => b.level - a.level
  || a.name.localeCompare(b.name, "zh-CN") || a.id.localeCompare(b.id);

export function attractionDisplay(items: Attraction[], region: RegionRecord, neighbors: RegionRecord[], bounds: MapBounds,
  selectedId: string | null = null): AttractionDisplay {
  const inView = items.filter((item) => containsPoint(bounds, [item.lng, item.lat])).sort(rank);
  if (region.level === "country") {
    return { primaryIds: inView.filter((item) => item.level >= 5).map((item) => item.id),
      nearbyNamedIds: [], nearbyUnnamedIds: [] };
  }
  const primary = inView.filter((item) => belongsToRegion(item, region));
  const leaf = region.level === "district" || region.childrenNum === 0;
  const shown = leaf ? primary : primary.slice(0, 20);
  // 用户主动选中的点占用本区域名额，不额外突破 20 个上限。
  const selected = primary.find((item) => item.id === selectedId);
  if (selected && !shown.includes(selected)) shown.splice(Math.max(0, shown.length - 1), 1, selected);
  const nearby = inView.filter((item) => !belongsToRegion(item, region)
    && neighbors.some((neighbor) => belongsToRegion(item, neighbor))).slice(0, 40);
  return {
    primaryIds: shown.map((item) => item.id),
    nearbyNamedIds: nearby.slice(0, 20).map((item) => item.id),
    nearbyUnnamedIds: nearby.slice(20).map((item) => item.id),
  };
}
