import type { FeatureCollection, Point } from "geojson";
import type { Attraction, BoundaryCollection, BoundaryFeature, MapBounds } from "../types";

const EARTH_RADIUS_KM = 6371;
const radians = (degrees: number) => degrees * Math.PI / 180;

export function pointInFeature(point: [number, number], feature: BoundaryFeature): boolean {
  const inRing = (ring: number[][]): boolean => {
    let inside = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const [x, y] = ring[i];
      const [px, py] = ring[j];
      const cross = (point[0] - x) * (py - y) - (point[1] - y) * (px - x);
      if (Math.abs(cross) < 1e-10 && point[0] >= Math.min(x, px) && point[0] <= Math.max(x, px)
        && point[1] >= Math.min(y, py) && point[1] <= Math.max(y, py)) return true;
      if ((y > point[1]) !== (py > point[1]) && point[0] < (px - x) * (point[1] - y) / (py - y) + x) inside = !inside;
    }
    return inside;
  };
  const polygons = feature.geometry.type === "Polygon" ? [feature.geometry.coordinates] : feature.geometry.coordinates;
  return polygons.some((rings) => inRing(rings[0]) && !rings.slice(1).some(inRing));
}

export function nearbyRegionBounds(bounds: MapBounds): MapBounds {
  // 周边随区域尺度外扩 20%，小区县至少外扩 20 公里，避免边界另一侧的景点突然消失。
  const latMargin = Math.max((bounds[3] - bounds[1]) * 0.2, 20 / EARTH_RADIUS_KM * 180 / Math.PI);
  const lngMargin = Math.max((bounds[2] - bounds[0]) * 0.2,
    20 / EARTH_RADIUS_KM * 180 / Math.PI / Math.cos(radians((bounds[1] + bounds[3]) / 2)));
  return [bounds[0] - lngMargin, bounds[1] - latMargin, bounds[2] + lngMargin, bounds[3] + latMargin];
}

function visibleLabelPoint(feature: BoundaryFeature, preferred: [number, number], viewport: MapBounds): [number, number] | null {
  if (preferred[0] >= viewport[0] && preferred[0] <= viewport[2]
    && preferred[1] >= viewport[1] && preferred[1] <= viewport[3]) return preferred;
  const bounds = featureBounds(feature);
  if (!intersects(bounds, viewport)) return null;
  const west = Math.max(bounds[0], viewport[0]);
  const south = Math.max(bounds[1], viewport[1]);
  const east = Math.min(bounds[2], viewport[2]);
  const north = Math.min(bounds[3], viewport[3]);
  // 城市中心在屏幕外时，在可见部分找一个位于实际多边形内的标注点。
  for (const y of [0.5, 0.3, 0.7, 0.1, 0.9]) {
    for (const x of [0.5, 0.3, 0.7, 0.1, 0.9]) {
      const point: [number, number] = [west + (east - west) * x, south + (north - south) * y];
      if (pointInFeature(point, feature)) return point;
    }
  }
  return null;
}

export function boundaryLabelPoints(data: BoundaryCollection, viewport?: MapBounds): FeatureCollection<Point> {
  return {
    type: "FeatureCollection",
    features: data.features.flatMap((feature) => {
      const { properties } = feature;
      const adcode = String(properties.adcode);
      const preferred = [properties.centroid, properties.center].find((point) => point
        && point.length === 2 && point.every(Number.isFinite)
        && Math.abs(point[0]) <= 180 && Math.abs(point[1]) <= 90);
      if (!properties.name?.trim() || !/^\d{6}$/.test(adcode) || !preferred) return [];
      const coordinates = viewport ? visibleLabelPoint(feature, preferred, viewport) : preferred;
      if (!coordinates) return [];
      // 每个区域只用一个标注点，避免多岛屿、多块边界重复标注名称。
      return [{
        type: "Feature" as const, id: adcode,
        properties: { adcode, name: properties.name },
        geometry: { type: "Point" as const, coordinates },
      }];
    }),
  };
}

export function nearbyAttractionBounds(target: Attraction, candidates: Attraction[]): MapBounds {
  // 至少展示周边 20 公里；再纳入 100 公里内最近的 3 处，避免孤立点把视野拉到全国。
  const latRadius = 20 / EARTH_RADIUS_KM * 180 / Math.PI;
  const lngRadius = latRadius / Math.cos(radians(target.lat));
  const bounds: MapBounds = [target.lng - lngRadius, target.lat - latRadius,
    target.lng + lngRadius, target.lat + latRadius];
  const nearby = candidates
    .filter((item) => item.id !== target.id)
    .map((item) => {
      const haversine = Math.sin(radians(item.lat - target.lat) / 2) ** 2
        + Math.cos(radians(target.lat)) * Math.cos(radians(item.lat))
        * Math.sin(radians(item.lng - target.lng) / 2) ** 2;
      return { item, distance: 2 * EARTH_RADIUS_KM * Math.asin(Math.sqrt(Math.min(1, haversine))) };
    })
    .filter(({ distance }) => distance <= 100)
    .sort((left, right) => left.distance - right.distance)
    .slice(0, 3);
  for (const { item } of nearby) {
    bounds[0] = Math.min(bounds[0], item.lng);
    bounds[1] = Math.min(bounds[1], item.lat);
    bounds[2] = Math.max(bounds[2], item.lng);
    bounds[3] = Math.max(bounds[3], item.lat);
  }
  return bufferedBounds(bounds);
}

export function featureBounds(feature: BoundaryFeature): MapBounds {
  const bounds: MapBounds = [Infinity, Infinity, -Infinity, -Infinity];
  const polygons = feature.geometry.type === "Polygon"
    ? [feature.geometry.coordinates]
    : feature.geometry.coordinates;
  for (const polygon of polygons) {
    for (const ring of polygon) {
      for (const [lng, lat] of ring) {
        bounds[0] = Math.min(bounds[0], lng);
        bounds[1] = Math.min(bounds[1], lat);
        bounds[2] = Math.max(bounds[2], lng);
        bounds[3] = Math.max(bounds[3], lat);
      }
    }
  }
  return bounds;
}

export function collectionBounds(data: BoundaryCollection): MapBounds {
  return data.features.reduce<MapBounds>((bounds, feature) => {
    const next = featureBounds(feature);
    return [Math.min(bounds[0], next[0]), Math.min(bounds[1], next[1]),
      Math.max(bounds[2], next[2]), Math.max(bounds[3], next[3])];
  }, [Infinity, Infinity, -Infinity, -Infinity]);
}

export function intersects(left: MapBounds, right: MapBounds): boolean {
  return left[0] <= right[2] && left[2] >= right[0]
    && left[1] <= right[3] && left[3] >= right[1];
}

export function bufferedBounds(bounds: MapBounds): MapBounds {
  const lng = (bounds[2] - bounds[0]) * 0.2;
  const lat = (bounds[3] - bounds[1]) * 0.2;
  return [bounds[0] - lng, bounds[1] - lat, bounds[2] + lng, bounds[3] + lat];
}
