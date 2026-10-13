export type BoundaryLevel = "province" | "city" | "district";

export interface AttractionSource {
  name: string;
  url: string;
  publishedAt: string;
}

export interface Attraction {
  id: string;
  name: string;
  province: string;
  city: string;
  district: string;
  adcode: string;
  address: string;
  lng: number;
  lat: number;
  level: number;
  levelSource: "official" | "manual";
  tags: string[];
  source: AttractionSource;
  coordinateSource?: {
    provider: "OpenStreetMap";
    name: string;
    url: string;
    externalId: string;
    license: string;
    licenseUrl: string;
    original: { lng: number; lat: number; coordSystem: "WGS84" };
    method: "mapped-point" | "point-on-surface";
    snapshotSha256: string;
  };
}

export interface AttractionDataset {
  schemaVersion: number;
  generatedAt: string;
  coordSystem: "GCJ-02";
  coverage: string[];
  items: Attraction[];
}

export interface AttractionFilters {
  levels: number[] | null;
  query: string;
}

export interface BoundaryFeatureProperties {
  name: string;
  adcode: number | string;
  level?: BoundaryLevel;
  parent?: { adcode?: number | string };
  center?: [number, number];
  centroid?: [number, number];
  childrenNum?: number;
}

export interface BoundaryFeature {
  type: "Feature";
  id?: string;
  properties: BoundaryFeatureProperties;
  geometry: {
    type: "Polygon";
    coordinates: number[][][];
  } | {
    type: "MultiPolygon";
    coordinates: number[][][][];
  };
}

export interface BoundaryCollection {
  type: "FeatureCollection";
  features: BoundaryFeature[];
}

export interface RegionContext {
  adcode: string;
  name: string;
  level: BoundaryLevel | "country";
}

export interface RegionTrailItem extends RegionContext {
  bounds: MapBounds;
}

export type MapBounds = [west: number, south: number, east: number, north: number];

export interface RegionRecord extends RegionTrailItem {
  parentAdcode?: string;
  childrenNum: number;
}

export interface MapViewport {
  bounds: MapBounds;
  zoom: number;
  center: [number, number];
}
