import { describe, expect, it } from "vitest";
import { attractionDisplay } from "../src/data/attractionDisplay";
import { filterAttractions } from "../src/data/attractionStore";
import { viewportZones } from "../src/map/viewportZones";
import type { Attraction, MapBounds } from "../src/types";

const visible: MapBounds = [120, 30, 122, 32];
const zones = viewportZones(visible);
const items = (prefix: string, count: number, lng: number, lat = 31): Attraction[] =>
  Array.from({ length: count }, (_, i) => ({
    id: `${prefix}-${i}`, name: `景点 ${i}`, adcode: prefix, level: i % 10, lng, lat,
    province: "", city: "", district: "", address: "", levelSource: "official", tags: [],
    source: { name: "", url: "", publishedAt: "" },
  }));
const allIds = (display: ReturnType<typeof attractionDisplay>) => [
  ...display.coreIds, ...display.edgeIds, ...display.bufferIds,
];
const projectedY = (lat: number) => Math.log(Math.tan(Math.PI / 4 + lat * Math.PI / 360));

describe("按屏幕中心、边缘和屏外缓冲展示景点", () => {
  it.each<MapBounds>([[120, 30, 122, 32], [97, 37, 127, 54], [120, 20, 122, 60]])(
    "中心宽高各为可见画面的 70%%，高纬和狭长视野仍按投影后的比例计算：%j", (...bounds) => {
      const split = viewportZones(bounds);
      const ratio = (inner: MapBounds) => [
        (inner[2] - inner[0]) / (bounds[2] - bounds[0]),
        (projectedY(inner[3]) - projectedY(inner[1])) / (projectedY(bounds[3]) - projectedY(bounds[1])),
      ];
      ratio(split.core).forEach((value) => expect(value).toBeCloseTo(0.7, 8));
      ratio(split.buffer).forEach((value) => expect(value).toBeCloseTo(1.5, 8));
      expect(split.visible).toEqual(bounds);
    });

  it("三组各取等级最高的 20 个，彼此独立、不重复，也不拿屏外点挤占屏内名额", () => {
    const data = [...items("310115", 65, 121), ...items("320500", 65, 120.1),
      ...items("330400", 65, 122.2), ...items("440103", 65, 130)];
    const result = attractionDisplay(data, zones);
    for (const group of [result.coreIds, result.edgeIds, result.bufferIds]) {
      expect(group).toHaveLength(20);
      const levels = group.map((id) => data.find((item) => item.id === id)!.level);
      expect(levels).toEqual([...levels].sort((a, b) => b - a));
      expect(Math.min(...levels)).toBeGreaterThanOrEqual(6);
    }
    expect(new Set(allIds(result)).size).toBe(60);
    expect(result.coreIds.every((id) => id.startsWith("310115"))).toBe(true);
    expect(result.edgeIds.every((id) => id.startsWith("320500"))).toBe(true);
    expect(result.bufferIds.every((id) => id.startsWith("330400"))).toBe(true);
    expect(allIds(result).some((id) => id.startsWith("440103"))).toBe(false);
  });

  it("同屏跨省、跨市、跨区的景点共用屏幕分组，不以行政归属或 5A 为硬门槛", () => {
    const data = ["150100", "130100", "310115", "440103"].flatMap((code) => items(code, 4, 121));
    expect(attractionDisplay(data, zones).coreIds).toHaveLength(16);
    const changedRegions = data.map((item) => ({ ...item, adcode: "999999" }));
    expect(attractionDisplay(changedRegions, zones)).toEqual(attractionDisplay(data, zones));
  });

  it("分区边界只计入一组，包含四个方向的边缘和缓冲点", () => {
    const positions = [
      [zones.core[0], 31], [zones.core[2], 31], [121, zones.core[1]], [121, zones.core[3]],
      [visible[0], 31], [visible[2], 31], [121, visible[1]], [121, visible[3]],
      [zones.buffer[0], 31], [zones.buffer[2], 31], [121, zones.buffer[1]], [121, zones.buffer[3]],
    ];
    const data = positions.map(([lng, lat], i) => ({ ...items("310115", 1, lng, lat)[0], id: String(i) }));
    const result = attractionDisplay(data, zones);
    expect(new Set(result.coreIds)).toEqual(new Set(["0", "1", "2", "3"]));
    expect(new Set(result.edgeIds)).toEqual(new Set(["4", "5", "6", "7"]));
    expect(new Set(result.bufferIds)).toEqual(new Set(["8", "9", "10", "11"]));
    expect(new Set(allIds(result)).size).toBe(12);
  });

  it("平移前已纳入缓冲的景点进入画面后直接补入边缘，旧视野外点及时移除", () => {
    const data = [...items("310115", 20, 121), ...items("320500", 20, 122.2), ...items("330400", 20, 119.6)];
    const before = attractionDisplay(data, zones);
    const entering = before.bufferIds.filter((id) => id.startsWith("320500"));
    expect(entering.length).toBeGreaterThan(0);
    const moved = attractionDisplay(data, viewportZones([120.4, 30, 122.4, 32]));
    expect(moved.coreIds).toHaveLength(20);
    expect(moved.edgeIds).toHaveLength(20);
    expect(entering.every((id) => moved.edgeIds.includes(id))).toBe(true);
    expect(allIds(moved).some((id) => id.startsWith("330400"))).toBe(false);
  });

  it("筛选仍生效，分区不足时按实际数量显示，空视野不补入遥远景点", () => {
    const data = [...items("310115", 8, 121), ...items("330400", 3, 122.2), ...items("440103", 80, 130)];
    const result = attractionDisplay(data, zones);
    expect(result.coreIds).toHaveLength(8);
    expect(result.edgeIds).toHaveLength(0);
    expect(result.bufferIds).toHaveLength(3);
    const filtered = filterAttractions(data, { levels: [2], query: "" });
    expect(allIds(attractionDisplay(filtered, zones))).toHaveLength(2);
    expect(allIds(attractionDisplay([], zones))).toHaveLength(0);
    expect(allIds(attractionDisplay(data, viewportZones([100, 30, 102, 32])))).toHaveLength(0);
  });

  it("同等级顺序稳定，轻微平移、输入顺序变化和返回原视野不会重新抽签", () => {
    const data = [...items("310115", 70, 121), ...items("320500", 70, 120.1)];
    const result = attractionDisplay(data, zones);
    expect(attractionDisplay([...data].reverse(), zones)).toEqual(result);
    expect(attractionDisplay(data, viewportZones([120.01, 30, 122.01, 32]))).toEqual(result);
    attractionDisplay(data, viewportZones([124, 30, 126, 32]));
    expect(attractionDisplay(data, zones)).toEqual(result);
  });

  it("选中的低等级点占用所在分区的名额，屏外遥远的选中点不突破缓冲范围", () => {
    for (const [lng, key] of [[121, "coreIds"], [120.1, "edgeIds"], [122.2, "bufferIds"]] as const) {
      const data = items("310115", 60, lng);
      const result = attractionDisplay(data, zones, data[0].id);
      expect(result[key]).toHaveLength(20);
      expect(result[key]).toContain(data[0].id);
    }
    const far = items("310115", 1, 130);
    expect(allIds(attractionDisplay(far, zones, far[0].id))).toHaveLength(0);
  });
});
