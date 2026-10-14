import { DIRECT_DISTRICT_CODES, ROOT_REGION } from "../config";
import { collectionBounds, featureAnchor, featureBounds, intersects, nearbyRegionBounds, pointInFeature } from "../map/geometry";
import { regionFitsViewport, regionTooSmallForViewport } from "../map/viewportSelection";
import { boundaryScope } from "../map/boundaryPresentation";
import type { BoundaryCollection, MapViewport, RegionRecord, RegionTrailItem } from "../types";

const BASE = "./data/boundaries/";
const CACHE_LIMIT = 48;
const EMPTY: BoundaryCollection = { type: "FeatureCollection", features: [] };

export class BoundaryLoadError extends Error {
  constructor(message: string, readonly retryable: boolean) {
    super(message);
  }
}

async function readJson<T>(path: string): Promise<T> {
  try {
    const response = await fetch(`${BASE}${path}`, { signal: AbortSignal.timeout(10_000) });
    if (!response.ok) {
      throw new BoundaryLoadError(`行政边界加载失败（${response.status}）`,
        response.status >= 500 || response.status === 408 || response.status === 429);
    }
    return await response.json() as T;
  } catch (error) {
    if (error instanceof BoundaryLoadError) throw error;
    if (error instanceof SyntaxError) throw new BoundaryLoadError("行政边界文件格式错误", false);
    throw new BoundaryLoadError("边界请求失败，请检查本地服务或网络连接", true);
  }
}

export class BoundaryRepository {
  private readonly cache = new Map<string, BoundaryCollection>();
  private readonly pending = new Map<string, Promise<BoundaryCollection>>();
  private readonly regions = new Map<string, RegionRecord>();
  private readonly missing = new Set<string>();
  private root: Promise<BoundaryCollection> | null = null;

  loadRoot(): Promise<BoundaryCollection> {
    if (!this.root) {
      this.root = Promise.all([
        this.read("provinces.geojson"),
        readJson<{ failures: { adcode?: string; province: string }[] }>("index.json").then((index) => {
          index.failures.forEach((item) => this.missing.add(item.adcode ?? item.province));
        }),
      ]).then(([data]) => {
        this.regions.set(ROOT_REGION.adcode, {
          ...ROOT_REGION, bounds: collectionBounds(data), childrenNum: 34,
        });
        return data;
      }).catch((error: unknown) => {
        this.root = null;
        throw error;
      });
    }
    return this.root;
  }

  getRegion(adcode: string): RegionRecord | undefined {
    return this.regions.get(adcode);
  }

  async regionAtViewport(viewport: MapViewport, anchor: [number, number], currentAdcode?: string): Promise<RegionRecord> {
    let data = await this.loadRoot();
    let region = this.getRegion(ROOT_REGION.adcode)!;
    if (regionFitsViewport(region, viewport, currentAdcode)) return region;
    // 从省到区县寻找合适层级；区域已缩得很小时保留上一级，不能只因能放进画面就一直选中。
    while (data.features.length) {
      const feature = data.features.find((item) => this.getRegion(String(item.properties.adcode)) && pointInFeature(anchor, item));
      if (!feature) break;
      const child = this.getRegion(String(feature.properties.adcode))!;
      if (regionTooSmallForViewport(child, viewport, currentAdcode)) break;
      region = child;
      if (regionFitsViewport(region, viewport, currentAdcode) || !this.childPath(region)) break;
      data = await this.loadChildren(region);
    }
    return region;
  }

  async nearbyRegions(region: RegionRecord, viewport: MapViewport): Promise<RegionRecord[]> {
    if (region.level === "country") return [];
    const nearbyBounds = nearbyRegionBounds(region.bounds);
    const isNearby = (candidate: RegionRecord) => intersects(candidate.bounds, nearbyBounds) && intersects(candidate.bounds, viewport.bounds);
    const root = await this.loadRoot();
    const provinces = this.collectionRegions(root).filter(isNearby);
    const scope = boundaryScope(region);
    let candidates: RegionRecord[];
    if (scope.detail === "province") {
      candidates = provinces;
    } else if (scope.detail === "city") {
      const parent = this.getRegion(scope.parentAdcode!);
      const siblings = parent ? this.collectionRegions(await this.loadChildren(parent)) : [];
      candidates = [...siblings, ...provinces.filter((province) => province.adcode !== scope.parentAdcode)];
    } else {
      const children = (await Promise.all(provinces.filter((province) => !DIRECT_DISTRICT_CODES.has(province.adcode))
        .map((province) => this.loadChildren(province))))
        .flatMap((data) => this.collectionRegions(data));
      const cities = [...provinces.filter((province) => DIRECT_DISTRICT_CODES.has(province.adcode)), ...children];
      const parent = this.getRegion(scope.parentAdcode!);
      const siblings = parent ? this.collectionRegions(await this.loadChildren(parent)) : [];
      candidates = [...cities.filter((city) => city.adcode !== scope.parentAdcode), ...siblings];
    }
    return [...new Map(candidates.map((candidate) => [candidate.adcode, candidate])).values()]
      .filter((candidate) => candidate.adcode !== region.adcode && isNearby(candidate));
  }

  async resolveRegion(adcode: string): Promise<RegionRecord | undefined> {
    await this.loadRoot();
    const known = this.getRegion(adcode);
    if (known) return known;
    const province = this.getRegion(adcode.slice(0, 2) + "0000");
    if (!province) return undefined;

    // 从全国列表直接点击景点时，下级边界可能还没有进入视口缓存。
    await this.loadChildren(province);
    const directChild = this.getRegion(adcode);
    if (directChild) return directChild;
    const city = this.getRegion(adcode.slice(0, 4) + "00");
    if (!city) return province;

    await this.loadChildren(city);
    // 景点与边界的区划版本可能不同，退回最小可用的上级区域。
    return this.getRegion(adcode) ?? city;
  }

  getTrail(adcode: string): RegionTrailItem[] {
    const trail: RegionRecord[] = [];
    const seen = new Set<string>();
    let current = this.regions.get(adcode);
    while (current && !seen.has(current.adcode)) {
      trail.unshift(current);
      seen.add(current.adcode);
      current = current.parentAdcode ? this.regions.get(current.parentAdcode) : undefined;
    }
    return trail;
  }

  childPath(region: RegionRecord): string | null {
    if (!region.childrenNum || this.missing.has(region.adcode)) return null;
    if (region.level === "province") {
      const folder = DIRECT_DISTRICT_CODES.has(region.adcode) ? "counties" : "cities";
      return `${folder}/${region.adcode}.geojson`;
    }
    return region.level === "city" ? `counties/${region.adcode}.geojson` : null;
  }

  loadChildren(region: RegionRecord): Promise<BoundaryCollection> {
    const path = this.childPath(region);
    return path ? this.read(path) : Promise.resolve(EMPTY);
  }

  private collectionRegions(data: BoundaryCollection): RegionRecord[] {
    // GeoJSON 也包含九段线等没有行政编码的要素。
    return data.features.flatMap((feature) => {
      const region = this.getRegion(String(feature.properties.adcode));
      return region ? [region] : [];
    });
  }

  private read(path: string): Promise<BoundaryCollection> {
    const cached = this.cache.get(path);
    if (cached) {
      this.cache.delete(path);
      this.cache.set(path, cached);
      return Promise.resolve(cached);
    }
    const pending = this.pending.get(path);
    if (pending) return pending;

    const request = readJson<BoundaryCollection>(path).then((data) => {
      if (data.type !== "FeatureCollection" || !Array.isArray(data.features)) {
        throw new BoundaryLoadError("行政边界文件格式错误", false);
      }
      for (const feature of data.features) {
        const properties = feature.properties;
        const adcode = String(properties.adcode);
        feature.id = adcode;
        properties.adcode = adcode;
        if (!/^\d{6}$/.test(adcode) || !properties.name || !properties.level) continue;
        this.regions.set(adcode, {
          adcode, name: properties.name, level: properties.level,
          parentAdcode: properties.parent?.adcode ? String(properties.parent.adcode) : undefined,
          bounds: featureBounds(feature), childrenNum: properties.childrenNum ?? 0, anchor: featureAnchor(feature),
        });
      }
      this.cache.set(path, data);
      while (this.cache.size > CACHE_LIMIT) this.cache.delete(this.cache.keys().next().value!);
      return data;
    }).finally(() => this.pending.delete(path));
    this.pending.set(path, request);
    return request;
  }
}
