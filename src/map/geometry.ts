import type { FeatureCollection, Point } from "geojson";
import type { Attraction, BoundaryCollection, BoundaryFeature, MapBounds } from "../types";

const EARTH_RADIUS_KM = 6371;
const radians = (degrees: number) => degrees * Math.PI / 180;

export function nearbyRegionBounds(bounds: MapBounds): MapBounds {
  // 周边随区域尺度外扩 20%，小区县至少外扩 20 公里，避免边界另一侧的景点突然消失。
  const latMargin = Math.max((bounds[3] - bounds[1]) * 0.2, 20 / EARTH_RADIUS_KM * 180 / Math.PI);
  const lngMargin = Math.max((bounds[2] - bounds[0]) * 0.2,
    20 / EARTH_RADIUS_KM * 180 / Math.PI / Math.cos(radians((bounds[1] + bounds[3]) / 2)));
  return [bounds[0] - lngMargin, bounds[1] - latMargin, bounds[2] + lngMargin, bounds[3] + latMargin];
}

export function boundaryLabelPoints(data: BoundaryCollection): FeatureCollection<Point> {
  return {
    type: "FeatureCollection",
    features: data.features.flatMap(({ properties }) => {
      const adcode = String(properties.adcode);
      const coordinates = [properties.centroid, properties.center].find((point) => point
        && point.length === 2 && point.every(Number.isFinite)
        && Math.abs(point[0]) <= 180 && Math.abs(point[1]) <= 90);
      if (!properties.name?.trim() || !/^\d{6}$/.test(adcode) || !coordinates) return [];
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
