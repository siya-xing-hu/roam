import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BoundaryRepository } from "../src/data/boundaryRepository";
import { presentedBoundaries } from "../src/map/boundaryPresentation";
import { boundaryLabelPoints, pointInFeature } from "../src/map/geometry";
import type { MapView } from "../src/map/mapView";
import { ViewportBoundaries } from "../src/map/viewportBoundaries";
import type { BoundaryCollection, BoundaryFeature, MapViewport, RegionRecord } from "../src/types";

const files = import.meta.glob<string>([
  "../public/data/boundaries/provinces.geojson", "../public/data/boundaries/index.json",
  "../public/data/boundaries/counties/310000.geojson", "../public/data/boundaries/counties/330400.geojson",
  "../public/data/boundaries/cities/{320000,330000}.geojson",
], { query: "?raw", import: "default", eager: true });
const viewport: MapViewport = { bounds: [120.4, 30.6, 122, 31.6], center: [121.2, 31.1], zoom: 11 };
const codes = (features: BoundaryFeature[]) => features.map((feature) => String(feature.properties.adcode));

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal("window", { addEventListener: vi.fn(), removeEventListener: vi.fn() });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const text = files["../public/" + String(input).replace(/^\.\//, "")];
    return new Response(text ?? "", { status: text ? 200 : 404 });
  }));
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("区域细节集中在选中地区的上级范围内", () => {
  it("城市中心在视野外时，嘉兴名称仍放在实际可见的城市范围内", async () => {
    const repo = new BoundaryRepository();
    await repo.resolveRegion("330400");
    const cities = await repo.loadChildren(repo.getRegion("330000")!);
    const jiaxing = cities.features.find((feature) => String(feature.properties.adcode) === "330400")!;
    const points = boundaryLabelPoints({ type: "FeatureCollection", features: [jiaxing] }, [120.847, 30.867, 121.729, 31.222]);
    expect(points.features).toHaveLength(1);
    const point = points.features[0].geometry.coordinates as [number, number];
    expect(point[0]).toBeGreaterThanOrEqual(120.847);
    expect(point[0]).toBeLessThanOrEqual(121.729);
    expect(point[1]).toBeGreaterThanOrEqual(30.867);
    expect(point[1]).toBeLessThanOrEqual(31.222);
    expect(pointInFeature(point, jiaxing)).toBe(true);
    expect(boundaryLabelPoints({ type: "FeatureCollection", features: [jiaxing] }, [100, 20, 101, 21]).features).toHaveLength(0);
  });

  it("松江视角只加载上海区县，市外边界和名称保持市级，且不受高 zoom 干扰", async () => {
    const repo = new BoundaryRepository();
    const root = await repo.loadRoot();
    const songjiang = (await repo.resolveRegion("310117"))!;
    const data: Record<string, BoundaryCollection> = {};
    const map = { setBoundaries: (level: string, collection: BoundaryCollection) => { data[level] = collection; } };
    const status = vi.fn();
    const loader = new ViewportBoundaries(repo, map as unknown as MapView, root, status);
    loader.update(viewport, songjiang);
    await vi.advanceTimersByTimeAsync(150);

    expect(codes(data.district.features)).toEqual(expect.arrayContaining(["310117", "310112", "310118"]));
    expect(data.district.features.every((feature) => String(feature.properties.parent?.adcode) === "310000")).toBe(true);
    expect(codes(presentedBoundaries(data.city.features, "city", songjiang, true)))
      .toEqual(expect.arrayContaining(["330400", "320500"]));
    expect(codes(presentedBoundaries(data.city.features, "city", songjiang, true))).not.toContain("310000");
    const jiaxing = repo.getRegion("330400")!;
    const externalCounties = await repo.loadChildren(jiaxing);
    expect(presentedBoundaries(externalCounties.features, "district", songjiang)).toHaveLength(0);
    expect(presentedBoundaries(externalCounties.features, "district", songjiang, true)).toHaveLength(0);
    // 唯一的外市区县请求来自本测试的显式读取，地图加载器没有展开它。
    expect(vi.mocked(fetch).mock.calls.filter(([url]) => String(url).includes("counties/330400"))).toHaveLength(1);
    expect(status).toHaveBeenLastCalledWith(null);
    loader.destroy();
  });

  it("切换城市、省和全国时清除旧细节；缓存中的嘉善县不会重新漏出来", async () => {
    const repo = new BoundaryRepository();
    const root = await repo.loadRoot();
    const songjiang = (await repo.resolveRegion("310117"))!;
    const jiashan = (await repo.resolveRegion("330421"))!;
    const jiaxing = repo.getRegion("330400")!;
    const data: Record<string, BoundaryCollection> = {};
    const map = { setBoundaries: (level: string, collection: BoundaryCollection) => { data[level] = collection; } };
    const loader = new ViewportBoundaries(repo, map as unknown as MapView, root, vi.fn());
    const show = async (region: RegionRecord) => { loader.update(viewport, region); await vi.advanceTimersByTimeAsync(150); };
    await show(jiashan);
    expect(codes(data.district.features)).toContain("330421");
    await show(songjiang);
    expect(codes(data.district.features)).not.toContain("330421");
    await show(jiaxing);
    expect(data.district.features).toHaveLength(0);
    expect(data.city.features.every((feature) => String(feature.properties.adcode).startsWith("33"))).toBe(true);
    const provinceNames = codes(presentedBoundaries(root.features, "province", jiaxing, true));
    expect(provinceNames).toEqual(expect.arrayContaining(["310000", "320000"]));
    expect(provinceNames).not.toContain("330000");
    await show(repo.getRegion("330000")!);
    expect(data.city.features).toHaveLength(0);
    await show(repo.getRegion("100000")!);
    expect(presentedBoundaries(root.features, "province", repo.getRegion("100000")!, true)).toHaveLength(root.features.length);
    loader.destroy();
  });
});
