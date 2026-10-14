import { DIRECT_DISTRICT_CODES } from "../config";
import type { BoundaryFeature, BoundaryLevel, RegionRecord } from "../types";

// 同一上级区域保留同级细节；跨出上级区域后，只展示粗一级的边界。
export function boundaryScope(region: RegionRecord | null): { detail: BoundaryLevel; parentAdcode?: string } {
  if (region?.level === "district") {
    const province = region.adcode.slice(0, 2) + "0000";
    return { detail: "district", parentAdcode: region.parentAdcode
      ?? (DIRECT_DISTRICT_CODES.has(province) ? province : region.adcode.slice(0, 4) + "00") };
  }
  if (region?.level === "city") {
    return { detail: "city", parentAdcode: region.parentAdcode ?? region.adcode.slice(0, 2) + "0000" };
  }
  // 直辖市使用省级边界文件；选中上海市时，周边直接显示江苏、浙江。
  return { detail: "province" };
}

export function presentedBoundaries(features: BoundaryFeature[], level: BoundaryLevel,
  region: RegionRecord | null, labels = false): BoundaryFeature[] {
  const scope = boundaryScope(region);
  return features.filter(({ properties }) => {
    const adcode = String(properties.adcode);
    if (level === "province") {
      return !labels || scope.detail === "province"
        || (scope.detail === "city" && adcode !== scope.parentAdcode);
    }
    if (level === "city") {
      if (scope.detail === "province") return false;
      if (scope.detail === "city") return adcode.slice(0, 2) === scope.parentAdcode?.slice(0, 2);
      return !labels || adcode !== scope.parentAdcode;
    }
    return scope.detail === "district" && String(properties.parent?.adcode) === scope.parentAdcode;
  });
}
