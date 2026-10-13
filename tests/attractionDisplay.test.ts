import { describe, expect, it } from "vitest";
import { attractionDisplay } from "../src/data/attractionDisplay";
import type { Attraction, RegionRecord } from "../src/types";

const region = (adcode: string, level: RegionRecord["level"], childrenNum = 0): RegionRecord => ({
  adcode, level, childrenNum, name: adcode, bounds: [120, 30, 122, 32],
});
const district = region("310115", "district");
const city = region("310000", "province", 16);
const neighbors = [region("320500", "city", 6), region("330400", "city", 6)];
const items = (adcode: string, count: number): Attraction[] => Array.from({ length: count }, (_, i) => ({
  id: `${adcode}-${i}`, name: `景点 ${i}`, adcode, level: i % 10, lng: 121, lat: 31,
  province: "", city: "", district: "", address: "", levelSource: "official", tags: [],
  source: { name: "", url: "", publishedAt: "" },
}));
const allIds = (display: ReturnType<typeof attractionDisplay>) => [
  ...display.primaryIds, ...display.nearbyNamedIds, ...display.nearbyUnnamedIds,
];

describe("随行政层级限制地图景点数量", () => {
  it("最小区域保留全部等级的点位，市级选最高等级的 20 个", () => {
    const own = items("310115", 65);
    expect(attractionDisplay(own, district, [], district.bounds).primaryIds).toHaveLength(65);
    const result = attractionDisplay(own, city, [], city.bounds);
    expect(result.primaryIds).toHaveLength(20);
    const levels = result.primaryIds.map((id) => own.find((item) => item.id === id)!.level);
    expect(levels).toEqual([...levels].sort((a, b) => b - a));
    expect(Math.min(...levels)).toBeGreaterThanOrEqual(6);
  });

  it("多个周边城市共用 20 个名称和 20 个点位的额度", () => {
    const own = items("310115", 65);
    const nearby = [...items("320501", 45), ...items("330401", 45)];
    const result = attractionDisplay([...own, ...nearby, ...items("440103", 50)], city, neighbors, city.bounds);
    expect(result.primaryIds).toHaveLength(20);
    expect(result.nearbyNamedIds).toHaveLength(20);
    expect(result.nearbyUnnamedIds).toHaveLength(20);
    expect(new Set(allIds(result)).size).toBe(60);
    expect(allIds(result).some((id) => id.startsWith("440103"))).toBe(false);
    const levels = [...result.nearbyNamedIds, ...result.nearbyUnnamedIds].map((id) => nearby.find((item) => item.id === id)!.level);
    expect(levels).toEqual([...levels].sort((a, b) => b - a));
  });

  it("画面外的高等级点不占名额，平移后从新视野重新选择", () => {
    const own = items("310115", 40).map((item, index) => ({ ...item, lng: index < 20 ? 121 : 125, level: index < 20 ? 1 : 9 }));
    const result = attractionDisplay(own, city, [], [120, 30, 122, 32]);
    expect(result.primaryIds).toHaveLength(20);
    expect(result.primaryIds.every((id) => own.find((item) => item.id === id)!.lng === 121)).toBe(true);
    const moved = attractionDisplay(own, city, [], [124, 30, 126, 32]);
    expect(moved.primaryIds).toHaveLength(20);
    expect(moved.primaryIds.every((id) => own.find((item) => item.id === id)!.lng === 125)).toBe(true);
  });

  it("区县按邻区归属筛选，周边数量不足时不拿远处景点补齐", () => {
    const data = [...items("310115", 3), ...items("310112", 7), ...items("310101", 12)];
    const result = attractionDisplay(data, district, [region("310112", "district")], district.bounds);
    expect(result.primaryIds).toHaveLength(3);
    expect(result.nearbyNamedIds).toHaveLength(7);
    expect(result.nearbyUnnamedIds).toHaveLength(0);
  });

  it("没有区县下级的城市按最小区域处理", () => {
    const own = items("442000", 45);
    expect(attractionDisplay(own, region("442000", "city"), [], city.bounds).primaryIds).toHaveLength(45);
  });

  it("全国显示所有 5 级及以上点位，数量超过 20 也不截断，不混入低等级选中点", () => {
    const data = [...items("310115", 100), ...items("320500", 100)];
    const result = attractionDisplay(data, region("100000", "country", 34), neighbors, city.bounds, data[0].id);
    expect(result.primaryIds).toHaveLength(100);
    expect(new Set(result.primaryIds)).toEqual(new Set(data.filter((item) => item.level >= 5).map((item) => item.id)));
    expect(result.nearbyNamedIds).toHaveLength(0);
    expect(result.nearbyUnnamedIds).toHaveLength(0);
  });

  it("同等级顺序稳定，选中点占用已有名额", () => {
    const own = items("310115", 60);
    const result = attractionDisplay(own, city, [], city.bounds, own[0].id);
    expect(result.primaryIds).toHaveLength(20);
    expect(result.primaryIds).toContain(own[0].id);
    expect(attractionDisplay([...own].reverse(), city, [], city.bounds, own[0].id)).toEqual(result);
  });
});
