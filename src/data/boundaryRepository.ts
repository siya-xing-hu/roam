import type { BoundaryCollection, BoundaryLevel } from "../types";

const ROOT_PATH = "./data/boundaries/provinces.geojson";

function childPath(level: BoundaryLevel, adcode: string): string {
  if (level === "city") {
    return `./data/boundaries/cities/${adcode}.geojson`;
  }

  return `./data/boundaries/counties/${adcode}.geojson`;
}

function isBoundaryCollection(value: unknown): value is BoundaryCollection {
  if (!value || typeof value !== "object") {
    return false;
  }

  const candidate = value as Partial<BoundaryCollection>;
  return candidate.type === "FeatureCollection" && Array.isArray(candidate.features);
}

export class BoundaryRepository {
  private readonly cache = new Map<string, Promise<BoundaryCollection>>();

  loadRoot(): Promise<BoundaryCollection> {
    return this.load(ROOT_PATH);
  }

  loadChildren(level: BoundaryLevel, parentAdcode: string): Promise<BoundaryCollection> {
    return this.load(childPath(level, parentAdcode));
  }

  private load(path: string): Promise<BoundaryCollection> {
    const cached = this.cache.get(path);
    if (cached) {
      return cached;
    }

    const request = fetch(path).then(async (response) => {
      if (!response.ok) {
        throw new Error(`行政边界加载失败（${response.status}）`);
      }

      const data = (await response.json()) as unknown;
      if (!isBoundaryCollection(data)) {
        throw new Error("行政边界文件格式错误");
      }

      return data;
    });

    this.cache.set(path, request);
    request.catch(() => this.cache.delete(path));
    return request;
  }
}
