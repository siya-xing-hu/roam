import type { BoundaryLevel } from "./types";

export const APP_NAME = "旅行地图";

export const ROOT_REGION = {
  adcode: "100000",
  name: "全国",
  level: "country" as const,
};

export const LEVEL_LABELS: Record<number, string> = {
  0: "未评级",
  1: "1 级",
  2: "2 级",
  3: "3 级",
  4: "4 级",
  5: "5 级",
  6: "精选 6",
  7: "精选 7",
  8: "精选 8",
  9: "最高精选",
};

export const LEVEL_COLORS: Record<number, string> = {
  0: "#7b8b86",
  1: "#77a894",
  2: "#5bb69a",
  3: "#34c6a1",
  4: "#f0bd62",
  5: "#ff7c5c",
  6: "#ff5d83",
  7: "#e95cff",
  8: "#a777ff",
  9: "#f9e27d",
};

export const DISPLAY_THRESHOLD: Record<BoundaryLevel | "country", number> = {
  country: 5,
  province: 4,
  city: 0,
  district: 0,
};

export const MUNICIPALITY_CODES = new Set(["110000", "120000", "310000", "500000"]);

export function nextBoundaryLevel(
  currentLevel: BoundaryLevel | "country",
  adcode: string,
): BoundaryLevel | null {
  if (currentLevel === "country") {
    return MUNICIPALITY_CODES.has(adcode) ? "district" : "city";
  }

  if (currentLevel === "province") {
    return "district";
  }

  return null;
}
