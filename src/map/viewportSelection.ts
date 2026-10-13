import { DIRECT_DISTRICT_CODES } from "../config";
import type { MapBounds, MapViewport, RegionContext, RegionRecord } from "../types";

export function displayRegionLevel(region: RegionContext): RegionContext["level"] {
  return DIRECT_DISTRICT_CODES.has(region.adcode) ? "city" : region.level;
}

export function containsPoint(bounds: MapBounds, point: [number, number]): boolean {
  return point[0] >= bounds[0] && point[0] <= bounds[2] && point[1] >= bounds[1] && point[1] <= bounds[3];
}

// 使用地图投影后的比例，避免同一行政区在不同纬度、屏幕尺寸下用固定 zoom 误判。
const mercatorY = (lat: number) => Math.log(Math.tan(Math.PI / 4 + Math.max(-85, Math.min(85, lat)) * Math.PI / 360));

export function regionFitsViewport(region: RegionRecord, viewport: MapViewport, currentAdcode?: string): boolean {
  const [west, south, east, north] = region.bounds;
  const [vw, vs, ve, vn] = viewport.bounds;
  const width = east - west;
  const height = mercatorY(north) - mercatorY(south);
  const span = Math.max(width / Math.max(ve - vw, 1e-8), height / Math.max(mercatorY(vn) - mercatorY(vs), 1e-8));
  const overlapWidth = Math.max(0, Math.min(east, ve) - Math.max(west, vw));
  const overlapHeight = Math.max(0, mercatorY(Math.min(north, vn)) - mercatorY(Math.max(south, vs)));
  // 已选层级留一点缓冲；在市/区临界处轻微缩放不会反复切换。
  const current = currentAdcode === region.adcode;
  return span <= (current ? 1.3 : 1.08) && overlapWidth * overlapHeight / Math.max(width * height, 1e-8) >= (current ? 0.55 : 0.75);
}

export function viewportAnchor(viewport: MapViewport, anchor: [number, number] | null): [number, number] {
  return anchor && containsPoint(viewport.bounds, anchor) ? anchor : viewport.center;
}
