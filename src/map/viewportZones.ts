import type { MapBounds } from "../types";

export interface ViewportZones {
  core: MapBounds;
  visible: MapBounds;
  buffer: MapBounds;
}

const projectLat = (lat: number) => Math.log(Math.tan(Math.PI / 4
  + Math.max(-85, Math.min(85, lat)) * Math.PI / 360));
const unprojectLat = (y: number) => (2 * Math.atan(Math.exp(y)) - Math.PI / 2) * 180 / Math.PI;

export function viewportZones(visible: MapBounds): ViewportZones {
  const [west, south, east, north] = visible;
  const width = east - west;
  const bottom = projectLat(south);
  const top = projectLat(north);
  const height = top - bottom;
  // 纬度先投影再切分，保证内蒙古等高纬地区也按屏幕像素比例划分。
  const scaledBounds = (margin: number): MapBounds => [
    west - width * margin, unprojectLat(bottom - height * margin),
    east + width * margin, unprojectLat(top + height * margin),
  ];
  return {
    core: scaledBounds(-0.15), // 中心宽、高各 70%，四周各留 15%。
    visible,
    buffer: scaledBounds(0.25), // 四个方向各提前渲染 1/4 屏的缓冲范围。
  };
}
