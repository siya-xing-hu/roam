import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { belongsToRegion } from "../src/data/attractionStore";
import { BoundaryRepository } from "../src/data/boundaryRepository";
import type { Attraction, RegionContext } from "../src/types";

const boundaryFiles = import.meta.glob<string>([
  "../public/data/boundaries/provinces.geojson",
  "../public/data/boundaries/index.json",
  "../public/data/boundaries/cities/{350000,420000,440000,450000}.geojson",
  "../public/data/boundaries/counties/{110000,350400,450100,500000}.geojson",
], { query: "?raw", import: "default", eager: true });

const readBoundary = async (input: RequestInfo | URL): Promise<Response> => {
  const text = boundaryFiles["../public/" + String(input).replace(/^\.\//, "")];
  return new Response(text ?? "", { status: text ? 200 : 404 });
};

beforeEach(() => vi.stubGlobal("fetch", vi.fn(readBoundary)));
afterEach(() => vi.unstubAllGlobals());

describe("景点所属区域按需加载", () => {
  it.each([
    ["450103", "青秀区", ["100000", "450000", "450100", "450103"]],
    ["110101", "东城区", ["100000", "110000", "110101"]],
    ["429006", "天门市", ["100000", "420000", "429006"]],
    ["442000", "中山市", ["100000", "440000", "442000"]],
  ])("从未加载的全国视图进入 %s", async (adcode, name, trail) => {
    const repository = new BoundaryRepository();
    const region = await repository.resolveRegion(adcode);
    expect(region?.name).toBe(name);
    expect(region?.adcode).toBe(adcode);
    expect(repository.getTrail(adcode).map((item) => item.adcode)).toEqual(trail);
  });

  it.each([
    ["350404", "350400", "三明市"],
    ["500157", "500000", "重庆市"],
  ])("区划编码 %s 没有对应边界时使用最小可用上级", async (adcode, expected, name) => {
    const region = await new BoundaryRepository().resolveRegion(adcode);
    expect(region?.adcode).toBe(expected);
    expect(region?.name).toBe(name);
  });

  it("下级边界加载失败后可重新点击加载", async () => {
    let fail = true;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      if (fail && String(input).endsWith("counties/450100.geojson")) {
        fail = false;
        throw new TypeError("network unavailable");
      }
      return readBoundary(input);
    }));
    const repository = new BoundaryRepository();
    await expect(repository.resolveRegion("450103")).rejects.toThrow("边界请求失败");
    expect((await repository.resolveRegion("450103"))?.name).toBe("青秀区");
  });
});

describe("名称和列表共用的区域归属", () => {
  const item = (adcode: string) => ({ adcode }) as Attraction;
  const region = (adcode: string, level: RegionContext["level"]): RegionContext => ({ adcode, level, name: "区域" });

  it("全国、省、市、区县逐级收窄", () => {
    expect(belongsToRegion(item("450103"), region("100000", "country"))).toBe(true);
    expect(belongsToRegion(item("450103"), region("450000", "province"))).toBe(true);
    expect(belongsToRegion(item("450103"), region("450100", "city"))).toBe(true);
    expect(belongsToRegion(item("450103"), region("450103", "district"))).toBe(true);
    expect(belongsToRegion(item("450105"), region("450103", "district"))).toBe(false);
    expect(belongsToRegion(item("450202"), region("450100", "city"))).toBe(false);
    expect(belongsToRegion(item("440103"), region("450000", "province"))).toBe(false);
  });

  it("不会把同省其他直辖县级区域当作同一座城市", () => {
    expect(belongsToRegion(item("429004"), region("429004", "city"))).toBe(true);
    expect(belongsToRegion(item("429006"), region("429004", "city"))).toBe(false);
    expect(belongsToRegion(item("469028"), region("469029", "city"))).toBe(false);
    expect(belongsToRegion(item("659003"), region("659002", "city"))).toBe(false);
  });
});
