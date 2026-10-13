export const APP_NAME = "足迹";

export const ROOT_REGION = {
  adcode: "100000",
  name: "全国",
  level: "country" as const,
};

export const LEVEL_LABELS: Record<number, string> = {
  0: "未评级",
  1: "1A",
  2: "2A",
  3: "3A",
  4: "4A",
  5: "5A",
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

export const DIRECT_DISTRICT_CODES = new Set([
  "110000", "120000", "310000", "500000", "810000", "820000",
]);

export const MAP_ZOOM = { province: 4, city: 6, district: 9 };
