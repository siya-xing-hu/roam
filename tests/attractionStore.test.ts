import { describe, expect, it } from "vitest";
import { belongsToRegion, isValidAttraction, visibleAttractions } from "../src/data/attractionStore";
import type { Attraction, RegionContext } from "../src/types";

const base: Attraction = {
  id: "demo",
  name: "示例景点",
  province: "上海市",
  city: "上海市",
  district: "浦东新区",
  adcode: "310115",
  address: "示例地址",
  lng: 121.5,
  lat: 31.2,
  level: 5,
  levelSource: "official",
  tags: ["5A"],
  source: { name: "官方来源", url: "https://example.com", publishedAt: "2026-01-01" },
};

describe("attractionStore", () => {
  it("validates the 0-9 level range", () => {
    expect(isValidAttraction(base)).toBe(true);
    expect(isValidAttraction({ ...base, level: 10 })).toBe(false);
  });

  it("matches province, city and district regions", () => {
    const province: RegionContext = { adcode: "310000", name: "上海市", level: "province" };
    const city: RegionContext = { adcode: "310100", name: "上海市", level: "city" };
    const district: RegionContext = { adcode: "310115", name: "浦东新区", level: "district" };
    expect(belongsToRegion(base, province)).toBe(true);
    expect(belongsToRegion(base, city)).toBe(true);
    expect(belongsToRegion(base, district)).toBe(true);
  });

  it("applies level thresholds while keeping curated levels visible", () => {
    const country: RegionContext = { adcode: "100000", name: "全国", level: "country" };
    const items = [
      { ...base, id: "level-4", level: 4, tags: ["4A"] },
      { ...base, id: "level-5", level: 5 },
      { ...base, id: "level-8", level: 8, levelSource: "manual" as const },
    ];
    expect(visibleAttractions(items, country).map((item) => item.level)).toEqual([8, 5]);
  });
});
