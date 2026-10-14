import { containsPoint } from "../map/viewportSelection";
import type { ViewportZones } from "../map/viewportZones";
import type { Attraction } from "../types";

export interface AttractionDisplay {
  coreIds: string[];
  edgeIds: string[];
  bufferIds: string[];
}

const names = new Intl.Collator("zh-CN");
const rank = (a: Attraction, b: Attraction) => b.level - a.level
  || names.compare(a.name, b.name) || a.id.localeCompare(b.id);

export function attractionDisplay(items: Attraction[], zones: ViewportZones,
  selectedId: string | null = null): AttractionDisplay {
  const core: Attraction[] = [];
  const edge: Attraction[] = [];
  const buffer: Attraction[] = [];
  for (const item of items) {
    const point: [number, number] = [item.lng, item.lat];
    if (!containsPoint(zones.buffer, point)) continue;
    if (containsPoint(zones.core, point)) core.push(item);
    else if (containsPoint(zones.visible, point)) edge.push(item);
    else buffer.push(item);
  }
  const select = (candidates: Attraction[]): string[] => {
    const shown = candidates.sort(rank).slice(0, 20);
    // 主动选中的点占用所在屏幕分区的一个名额，不额外增加点数。
    const selected = selectedId ? candidates.find((item) => item.id === selectedId) : undefined;
    if (selected && !shown.includes(selected)) shown[shown.length - 1] = selected;
    return shown.map((item) => item.id);
  };
  return { coreIds: select(core), edgeIds: select(edge), bufferIds: select(buffer) };
}
