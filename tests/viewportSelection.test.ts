import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BoundaryRepository } from "../src/data/boundaryRepository";
import { pointInFeature } from "../src/map/geometry";
import { displayRegionLevel, regionFitsViewport, viewportAnchor } from "../src/map/viewportSelection";
import type { BoundaryFeature, MapBounds, MapViewport } from "../src/types";

const files = import.meta.glob<string>([
  "../public/data/boundaries/provinces.geojson", "../public/data/boundaries/index.json",
  "../public/data/boundaries/counties/310000.geojson",
  "../public/data/boundaries/cities/{320000,330000,450000}.geojson",
], { query: "?raw", import: "default", eager: true });
beforeEach(() => vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
  const text = files["../public/" + String(input).replace(/^\.\//, "")];
  return new Response(text ?? "", { status: text ? 200 : 404 });
})));
afterEach(() => vi.unstubAllGlobals());

function view(bounds: MapBounds, zoom: number, scale = 1.03): MapViewport {
  const center: [number, number] = [(bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2];
  const w = (bounds[2] - bounds[0]) * scale / 2;
  const h = (bounds[3] - bounds[1]) * scale / 2;
  return { center, zoom, bounds: [center[0] - w, center[1] - h, center[0] + w, center[1] + h] };
}
const pearl: [number, number] = [121.4997, 31.2397];

describe("缩放时自动切换所选行政区域", () => {
  it("浦东新区 → 上海市 → 全国，放大后能回到浦东新区", async () => {
    const repo = new BoundaryRepository();
    const district = (await repo.resolveRegion("310115"))!;
    const city = repo.getRegion("310000")!;
    expect((await repo.regionAtViewport(view(district.bounds, 10), pearl, district.adcode)).adcode).toBe("310115");
    expect((await repo.regionAtViewport(view(city.bounds, 8), pearl, district.adcode)).adcode).toBe("310000");
    expect(displayRegionLevel(city)).toBe("city");
    expect((await repo.regionAtViewport(view(repo.getRegion("100000")!.bounds, 3), pearl, city.adcode)).adcode).toBe("100000");
    expect((await repo.regionAtViewport(view(district.bounds, 10), pearl, "100000")).adcode).toBe("310115");
  });

  it("选中上海市时周边提高到省级，包含江苏、浙江", async () => {
    const repo = new BoundaryRepository();
    await repo.loadRoot();
    const city = repo.getRegion("310000")!;
    const adjacent = await repo.nearbyRegions(city, view(city.bounds, 8, 1.6));
    expect(adjacent.map((r) => r.adcode)).toEqual(expect.arrayContaining(["320000", "330000"]));
    expect(adjacent.some((r) => r.adcode.startsWith("31"))).toBe(false);
    expect(adjacent.every((r) => r.level === "province")).toBe(true);
  });

  it("选中松江保留上海其他区，跨市使用嘉兴和苏州，不加载嘉善县", async () => {
    const repo = new BoundaryRepository();
    const songjiang = (await repo.resolveRegion("310117"))!;
    const adjacent = await repo.nearbyRegions(songjiang, view(songjiang.bounds, 10, 2));
    const codes = adjacent.map((r) => r.adcode);
    expect(codes).toEqual(expect.arrayContaining(["310112", "310118", "320500", "330400"]));
    expect(codes).not.toContain("310117");
    expect(codes).not.toContain("310000");
    expect(codes).not.toContain("330421");
    expect(adjacent.filter((r) => !r.adcode.startsWith("31")).every((r) => displayRegionLevel(r) === "city")).toBe(true);
    expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes("counties/330400"))).toBe(false);
  });

  it("选中嘉兴时浙江内部保留市，省外显示上海和江苏", async () => {
    const repo = new BoundaryRepository();
    const jiaxing = (await repo.resolveRegion("330400"))!;
    const adjacent = await repo.nearbyRegions(jiaxing, view(jiaxing.bounds, 8, 2));
    const codes = adjacent.map((r) => r.adcode);
    expect(codes).toEqual(expect.arrayContaining(["330100", "330500", "310000", "320000"]));
    expect(codes).not.toContain("330400");
    expect(codes).not.toContain("330000");
    expect(codes).not.toContain("320500");
  });

  it("普通省份保留省级范围，临界小幅缩放不会反复跳级", async () => {
    const repo = new BoundaryRepository();
    await repo.loadRoot();
    const province = repo.getRegion("450000")!;
    expect((await repo.regionAtViewport(view(province.bounds, 6), [108.4, 22.8])).adcode).toBe("450000");
    const city = repo.getRegion("310000")!;
    const slightlyCloser = view(city.bounds, 8.3, 0.87);
    expect(regionFitsViewport(city, slightlyCloser, city.adcode)).toBe(true);
    expect(regionFitsViewport(city, slightlyCloser, "310115")).toBe(false);
  });

  it("选中目标仍在视野内时保持锚点，移出视野后使用画面中心", () => {
    const viewport = view([120, 30, 122, 32], 9);
    expect(viewportAnchor(viewport, pearl)).toEqual(pearl);
    expect(viewportAnchor(viewport, [108, 22])).toEqual(viewport.center);
  });

  it("定位使用实际多边形而非外接矩形，并排除洞", () => {
    const feature: BoundaryFeature = { type: "Feature", properties: { adcode: "310000", name: "测试" },
      geometry: { type: "Polygon", coordinates: [
        [[0, 0], [4, 0], [4, 4], [0, 0]],
        [[2, 0.5], [3, 0.5], [3, 1], [2, 1], [2, 0.5]],
      ] } };
    expect(pointInFeature([3, 2], feature)).toBe(true);
    expect(pointInFeature([1, 3], feature)).toBe(false);
    expect(pointInFeature([2.5, 0.7], feature)).toBe(false);
  });
});
