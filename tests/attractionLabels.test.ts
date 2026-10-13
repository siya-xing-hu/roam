import { describe, expect, it } from "vitest";
import { attractionLabelScope } from "../src/data/attractionStore";
import type { Attraction, RegionTrailItem } from "../src/types";

const district: RegionTrailItem = {
  adcode: "110101", name: "东城区", level: "district", bounds: [116.39, 39.86, 116.46, 39.97],
};
const point = (id: string, adcode: string, lng: number, lat: number): Attraction => ({
  id, adcode, lng, lat, name: id, province: "", city: "", district: "", address: "",
  level: 5, levelSource: "official", tags: [], source: { name: "", url: "", publishedAt: "" },
});

describe("区域与周边景点名称", () => {
  it("包含邻区的近处景点，排除远处景点，并区分显示优先级", () => {
    const items = [
      point("本区", "110101", 116.41, 39.91),
      point("邻区", "110102", 116.37, 39.91),
      point("远处", "110105", 117.4, 40),
    ];
    expect(attractionLabelScope(items, district)).toEqual({ primaryIds: ["本区"], nearbyIds: ["邻区"] });
    expect(attractionLabelScope(items, null)).toEqual({ primaryIds: ["本区", "邻区", "远处"], nearbyIds: [] });
  });

  it("周边范围按所选区域计算，切换区域后不会保留旧区域附近的名称", () => {
    const other: RegionTrailItem = { adcode: "450103", name: "青秀区", level: "district", bounds: [108.3, 22.7, 108.6, 23] };
    const items = [point("北京", "110102", 116.37, 39.91), point("南宁周边", "450105", 108.25, 22.85)];
    expect(attractionLabelScope(items, district).nearbyIds).toEqual(["北京"]);
    expect(attractionLabelScope(items, other).nearbyIds).toEqual(["南宁周边"]);
  });

  it("省市尺度的周边范围随区域大小扩大", () => {
    const large: RegionTrailItem = { adcode: "450000", name: "区域", level: "province", bounds: [100, 25, 110, 35] };
    const items = [point("邻近", "440103", 111, 30), point("较远", "440105", 113, 30)];
    expect(attractionLabelScope(items, large).nearbyIds).toEqual(["邻近"]);
  });

  it("高纬度地区仍能包含东西方向约 20 公里内的周边景点", () => {
    const north: RegionTrailItem = { ...district, bounds: [120, 53, 120.1, 53.1] };
    const items = [point("附近", "230101", 120.38, 53.05), point("远处", "230102", 120.5, 53.05)];
    expect(attractionLabelScope(items, north).nearbyIds).toEqual(["附近"]);
  });
});
