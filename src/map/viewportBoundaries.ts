import { DIRECT_DISTRICT_CODES } from "../config";
import { BoundaryLoadError, BoundaryRepository } from "../data/boundaryRepository";
import type { BoundaryCollection, BoundaryFeature, MapViewport, RegionRecord } from "../types";
import { bufferedBounds, intersects } from "./geometry";
import type { MapView } from "./mapView";
import { boundaryScope } from "./boundaryPresentation";

const RETRY_DELAYS = [1_000, 3_000, 8_000];
interface LoadFailure { message: string; attempts: number; retryAt: number }

export class ViewportBoundaries {
  private viewport: MapViewport | null = null;
  private selectedRegion: RegionRecord | null = null;
  private revision = 0;
  private running = false;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private retryTimer: ReturnType<typeof setTimeout> | undefined;
  private readonly failures = new Map<string, LoadFailure>();
  private readonly onOnline = () => this.retry();
  private readonly signatures = { city: "", district: "" };

  constructor(
    private readonly repository: BoundaryRepository,
    private readonly mapView: MapView,
    private readonly root: BoundaryCollection,
    private readonly onStatus: (error: string | null) => void,
  ) {
    window.addEventListener("online", this.onOnline);
  }

  update(viewport: MapViewport, region: RegionRecord | null = this.selectedRegion): void {
    this.viewport = viewport;
    this.selectedRegion = region;
    this.revision += 1;
    this.schedule();
  }

  retry(): void {
    this.failures.clear();
    if (this.viewport) this.update(this.viewport);
  }

  destroy(): void {
    this.revision += 1;
    this.viewport = null;
    clearTimeout(this.timer);
    clearTimeout(this.retryTimer);
    window.removeEventListener("online", this.onOnline);
  }

  private schedule(): void {
    if (this.running || this.timer || !this.viewport) return;
    this.timer = setTimeout(() => {
      this.timer = undefined;
      void this.refresh();
    }, 120);
  }

  private async refresh(): Promise<void> {
    if (!this.viewport) return;
    clearTimeout(this.retryTimer);
    this.running = true;
    const revision = this.revision;
    const viewport = this.viewport;
    const bounds = bufferedBounds(viewport.bounds);
    const scope = boundaryScope(this.selectedRegion);
    const cities: BoundaryFeature[] = [];
    const districts: BoundaryFeature[] = [];
    const errors: string[] = [];
    const requestedPaths = new Set<string>();

    try {
      if (scope.detail !== "province") {
        const provinces = this.root.features.flatMap((feature) => {
          const region = this.repository.getRegion(String(feature.properties.adcode));
          return region && (scope.detail === "city" ? region.adcode === scope.parentAdcode
            : intersects(region.bounds, bounds)) ? [region] : [];
        });
        // 区县视角只加载目标城市的区县，其他城市不再递归展开。
        const provinceChildren = await this.loadMany(provinces.filter((province) =>
          !DIRECT_DISTRICT_CODES.has(province.adcode)), revision, errors, requestedPaths);
        if (revision !== this.revision) return;

        if (scope.detail === "district") {
          cities.push(...this.root.features.filter((feature) => DIRECT_DISTRICT_CODES.has(String(feature.properties.adcode))));
        }
        for (const data of provinceChildren) {
          for (const feature of data.features) {
            const region = this.repository.getRegion(String(feature.properties.adcode));
            if (!region) continue;
            cities.push(feature);
          }
        }
        if (scope.detail === "district" && scope.parentAdcode) {
          const parent = this.repository.getRegion(scope.parentAdcode);
          const cityChildren = await this.loadMany(parent ? [parent] : [], revision, errors, requestedPaths);
          for (const data of cityChildren) districts.push(...data.features.filter((feature) => feature.properties.level === "district"));
        }
      }
      if (revision !== this.revision) return;
      this.apply("city", cities);
      this.apply("district", districts);
      const nextRetry = Math.min(...[...requestedPaths].map((path) => this.failures.get(path)?.retryAt ?? Infinity));
      this.onStatus(errors.length ? errors[0] + (Number.isFinite(nextRetry)
        ? "；稍后自动重试，已有地图仍可浏览"
        : "；自动重试已停止，可点击重试，已有地图仍可浏览") : null);
      if (Number.isFinite(nextRetry)) {
        this.retryTimer = setTimeout(() => {
          if (this.viewport) this.update(this.viewport);
        }, Math.max(0, nextRetry - Date.now()));
      }
    } finally {
      this.running = false;
      if (revision !== this.revision) this.schedule();
    }
  }

  private async loadMany(regions: RegionRecord[], revision: number, errors: string[], requestedPaths: Set<string>): Promise<BoundaryCollection[]> {
    const results: BoundaryCollection[] = new Array(regions.length);
    let cursor = 0;
    await Promise.all(Array.from({ length: Math.min(4, regions.length) }, async () => {
      while (cursor < regions.length && revision === this.revision) {
        const index = cursor++;
        const region = regions[index];
        const path = this.repository.childPath(region);
        if (!path) continue;
        requestedPaths.add(path);
        const previousFailure = this.failures.get(path);
        if (previousFailure && Date.now() < previousFailure.retryAt) {
          errors.push(previousFailure.message);
          continue;
        }
        try {
          results[index] = await this.repository.loadChildren(region);
          this.failures.delete(path);
        } catch (error) {
          const message = region.name + "：" + (error instanceof Error ? error.message : "边界加载失败");
          const attempts = (previousFailure?.attempts ?? 0) + 1;
          const delay = RETRY_DELAYS[attempts - 1];
          const retryable = !(error instanceof BoundaryLoadError) || error.retryable;
          this.failures.set(path, { message, attempts,
            retryAt: retryable && delay !== undefined ? Date.now() + delay : Infinity });
          errors.push(message);
        }
      }
    }));
    return results.filter(Boolean);
  }

  private apply(level: "city" | "district", features: BoundaryFeature[]): void {
    const signature = features.map((feature) => feature.id).sort().join(",");
    if (signature === this.signatures[level]) return;
    this.signatures[level] = signature;
    this.mapView.setBoundaries(level, { type: "FeatureCollection", features });
  }
}
