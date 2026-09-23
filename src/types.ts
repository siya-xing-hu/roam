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
}

export interface AttractionDataset {
  schemaVersion: number;
  generatedAt: string;
  coordSystem: "GCJ-02";
  coverage: string[];
  items: Attraction[];
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
  properties: BoundaryFeatureProperties;
  geometry: {
    type: "Polygon" | "MultiPolygon";
    coordinates: unknown;
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
  mapData: BoundaryCollection;
}
