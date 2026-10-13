import { LngLat, Map as LibreMap, Popup, setWorkerUrl, type FilterSpecification, type GeoJSONSource, type MapGeoJSONFeature, type PaddingOptions } from "maplibre-gl";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import "maplibre-gl/dist/maplibre-gl.css";
import type { FeatureCollection, Point } from "geojson";
import { LEVEL_COLORS, MAP_ZOOM, minimumAttractionLevel } from "../config";
import { attractionLabelScope } from "../data/attractionStore";
import type { Attraction, BoundaryCollection, BoundaryLevel, MapViewport, RegionTrailItem } from "../types";
import { boundaryLabelPoints } from "./geometry";

const LEVELS: BoundaryLevel[] = ["province", "city", "district"];
const EMPTY: BoundaryCollection = { type: "FeatureCollection", features: [] };
const FONT = ["sans-serif"];

setWorkerUrl(workerUrl);

interface MapCallbacks {
  onRegionClick: (adcode: string) => void;
  onAttractionClick: (attraction: Attraction) => void;
  onViewportChange: (viewport: MapViewport) => void;
  onAttractionLevelChange: (minimumLevel: number) => void;
  onError: (message: string) => void;
}

export class MapView {
  readonly ready: Promise<void>;
  private readonly map: LibreMap;
  private readonly resizeObserver: ResizeObserver;
  private readonly popup = new Popup({ closeButton: false, closeOnClick: false, offset: 12, className: "map-tooltip" });
  private readonly selectedPopup = new Popup({ closeButton: false, closeOnClick: false, offset: 16,
    maxWidth: "240px", className: "map-tooltip selected-tooltip" });
  private readonly attractions = new Map<string, Attraction>();
  private filteredAttractions: Attraction[] = [];
  private displayedMinimumLevel: number | null = null;
  private initialized = false;
  private resizeFrame = 0;
  private selectedRegion: RegionTrailItem | null = null;
  private selectedAttraction: string | null = null;
  private hovered: { source: string; id: string | number } | null = null;
  private tooltipId = "";

  constructor(container: HTMLElement, private readonly callbacks: MapCallbacks) {
    this.map = new LibreMap({
      container,
      style: {
        version: 8,
        sources: {},
        layers: [{ id: "background", type: "background", paint: { "background-color": "#071210" } }],
      },
      center: [105, 35], zoom: 3, minZoom: 0, maxZoom: 17,
      attributionControl: { compact: false },
      renderWorldCopies: false,
      // 允许视野边缘留白，否则国际日期线约束会把已避开面板的全国视图推回右侧。
      transformConstrain: (center, zoom) => ({
        center: new LngLat(Math.max(-180, Math.min(180, center.lng)), Math.max(-85, Math.min(85, center.lat))),
        zoom: Math.max(0, Math.min(17, zoom)),
      }),
      dragRotate: false, pitchWithRotate: false, touchPitch: false,
      maxPitch: 0,
      localIdeographFontFamily: '"PingFang SC", "Microsoft YaHei", sans-serif',
      fadeDuration: 120,
      maxTileCacheSize: 128,
      locale: { "Map.Title": "全国行政区划与景点地图" },
    });
    this.map.touchZoomRotate.disableRotation();
    this.map.keyboard.disableRotation();
    this.ready = new Promise((resolve, reject) => {
      const onInitialError = () => reject(new Error("地图初始化失败，请确认浏览器支持 WebGL 和硬件加速"));
      this.map.once("error", onInitialError);
      this.map.once("load", () => {
        this.map.off("error", onInitialError);
        resolve();
      });
    });
    this.map.on("error", () => {
      if (this.initialized) this.callbacks.onError("地图绘制失败，请刷新页面或检查浏览器硬件加速设置");
    });
    this.resizeObserver = new ResizeObserver(() => {
      cancelAnimationFrame(this.resizeFrame);
      this.resizeFrame = requestAnimationFrame(() => this.map.resize());
    });
    this.resizeObserver.observe(container);
    this.map.on("move", () => {
      this.popup.remove();
      this.clearHover();
      if (this.initialized) this.callbacks.onViewportChange(this.getViewport());
    });
    this.map.on("moveend", () => {
      if (this.initialized) this.callbacks.onViewportChange(this.getViewport());
    });
    this.map.on("zoom", () => this.updateAttractionVisibility());
    this.map.on("click", (event) => {
      if (!this.initialized) return;
      const features = this.featuresAt(event.point);
      const point = features.find((feature) => feature.source === "attractions");
      if (point) {
        const attraction = this.attractions.get(String(point.id));
        if (attraction) this.callbacks.onAttractionClick(attraction);
        return;
      }
      for (const level of [...LEVELS].reverse()) {
        const region = features.find((feature) => feature.source === level);
        const adcode = String(region?.properties.adcode ?? "");
        if (/^\d{6}$/.test(adcode)) {
          this.callbacks.onRegionClick(adcode);
          return;
        }
      }
    });
    this.map.on("mousemove", (event) => {
      if (!this.initialized || this.map.isMoving()) return;
      const features = this.featuresAt(event.point);
      const point = features.find((feature) => feature.source === "attractions");
      const feature = point ?? [...LEVELS].reverse().flatMap((level) =>
        features.filter((item) => item.source === level),
      )[0];
      if (!feature || !feature.properties.name) {
        this.popup.remove();
        this.clearHover();
        return;
      }
      this.map.getCanvas().style.cursor = "pointer";
      const source = feature.source;
      const id = feature.id!;
      if (this.hovered?.id !== id || this.hovered?.source !== source) {
        this.clearHover();
        this.map.getCanvas().style.cursor = "pointer";
        this.hovered = { source, id };
        this.map.setFeatureState(this.hovered, { hover: true });
      }
      if (point) {
        const attraction = this.attractions.get(String(point.id));
        if (!attraction) return;
        if (attraction.id === this.selectedAttraction) {
          this.popup.remove();
          return;
        }
        if (this.tooltipId !== attraction.id) {
          const content = document.createElement("div");
          const title = document.createElement("strong");
          title.textContent = attraction.name;
          content.append(title);
          this.popup.setDOMContent(content);
          this.tooltipId = attraction.id;
        }
        this.popup.setLngLat(event.lngLat).addTo(this.map);
      } else {
        this.popup.remove();
      }
    });
    this.map.getCanvas().addEventListener("mouseleave", () => {
      this.popup.remove();
      this.clearHover();
    });
  }

  initialize(root: BoundaryCollection, attractions: Attraction[]): void {
    if (this.initialized) return;
    for (const level of LEVELS) {
      const data = level === "province" ? root : EMPTY;
      const minzoom = level === "province" ? 0 : MAP_ZOOM[level];
      this.map.addSource(level, { type: "geojson", data, promoteId: "adcode", maxzoom: 12, tolerance: 0.5 });
      this.map.addLayer({
        id: level + "-fill", type: "fill", source: level, minzoom,
        paint: {
          "fill-color": level === "province" ? "#102823" : "#45856e",
          "fill-opacity": level === "province" ? 1 : [
            "case", ["boolean", ["feature-state", "hover"], false], 0.3,
            ["boolean", ["feature-state", "selected"], false], 0.2, 0.025,
          ],
        },
      });
      this.map.addLayer({
        id: level + "-line", type: "line", source: level, minzoom,
        paint: {
          "line-color": ["case", ["boolean", ["feature-state", "selected"], false], "#87dec0",
            ["boolean", ["feature-state", "hover"], false], "#91bcac",
            level === "province" ? "#608576" : "#3d6357"],
          "line-width": level === "province" ? 1.1 : level === "city" ? 0.8 : 0.6,
        },
      });
    }
    attractions.forEach((attraction) => this.attractions.set(attraction.id, attraction));
    this.filteredAttractions = attractions;
    const data: FeatureCollection<Point> = {
      type: "FeatureCollection",
      features: attractions.map((attraction) => ({
        type: "Feature", id: attraction.id,
        properties: {
          id: attraction.id, name: attraction.name, level: attraction.level,
          color: LEVEL_COLORS[attraction.level],
        },
        geometry: { type: "Point", coordinates: [attraction.lng, attraction.lat] },
      })),
    };
    this.map.addSource("attractions", {
      type: "geojson", data, promoteId: "id", maxzoom: 12,
      ...(attractions.some((item) => item.coordinateSource?.provider === "OpenStreetMap")
        ? { attribution: '部分景点坐标 © <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap contributors</a>' }
        : {}),
    });
    this.map.addLayer({
      id: "attraction-points", type: "circle", source: "attractions",
      paint: {
        "circle-color": ["get", "color"],
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 3, 2.6, 6, 4, 10, 6, 14, 7],
        "circle-stroke-color": "#fff5e9",
        "circle-stroke-width": [
          "case", ["boolean", ["feature-state", "selected"], false], 2.5,
          ["boolean", ["feature-state", "hover"], false], 2, 0.7,
        ],
        "circle-opacity": 0.95,
      },
    });
    this.map.addLayer({
      id: "attraction-labels", type: "symbol", source: "attractions",
      layout: {
        "text-field": ["get", "name"], "text-font": FONT,
        "text-size": ["interpolate", ["linear"], ["zoom"], 3, 10, 10, 12],
        "text-variable-anchor": ["top", "bottom", "left", "right"],
        "text-radial-offset": 0.9, "text-max-width": 12, "text-padding": 6,
        "text-allow-overlap": false,
        "symbol-sort-key": ["-", 9, ["get", "level"]],
      },
      paint: { "text-color": "#e6eee8", "text-halo-color": "#071210", "text-halo-width": 2 },
    });
    // 区域名称优先参与碰撞检测，景点名称自动避让，保留清晰的地理参照。
    for (const level of LEVELS) {
      this.map.addSource(level + "-label-points", {
        type: "geojson", data: boundaryLabelPoints(level === "province" ? root : EMPTY),
        promoteId: "adcode", maxzoom: 12,
      });
      this.map.addLayer({
        id: level + "-labels", type: "symbol", source: level + "-label-points",
        minzoom: level === "province" ? 0 : MAP_ZOOM[level],
        maxzoom: level === "province" ? MAP_ZOOM.city : level === "city" ? MAP_ZOOM.district : 24,
        layout: {
          "text-field": ["get", "name"], "text-font": FONT,
          "text-size": level === "province" ? 14 : level === "city" ? 13 : 12,
          "text-max-width": 8, "text-padding": 5,
          "text-allow-overlap": false,
        },
        paint: {
          "text-color": level === "province" ? "#bcd9cb" : level === "city" ? "#accdbc" : "#9bbcaa",
          "text-halo-color": "#071210", "text-halo-width": 2,
        },
      });
    }
    this.initialized = true;
    this.updateAttractionVisibility();
  }

  setBoundaries(level: "city" | "district", data: BoundaryCollection): void {
    this.clearHover();
    (this.map.getSource(level) as GeoJSONSource).setData(data);
    (this.map.getSource(level + "-label-points") as GeoJSONSource).setData(boundaryLabelPoints(data));
  }

  getViewport(): MapViewport {
    const bounds = this.map.getBounds();
    return { bounds: [bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth()], zoom: this.map.getZoom() };
  }

  focusRegion(region: RegionTrailItem, padding: PaddingOptions, immediate = false): void {
    this.popup.remove();
    this.clearAttractionSelection();
    for (const level of LEVELS) {
      if (this.selectedRegion) this.map.removeFeatureState({ source: level, id: this.selectedRegion.adcode }, "selected");
      if (region.level !== "country") this.map.setFeatureState({ source: level, id: region.adcode }, { selected: true });
    }
    this.selectedRegion = region.level === "country" ? null : region;
    this.updateAttractionVisibility(true);
    const [west, south, east, north] = region.bounds;
    this.map.fitBounds([[west, south], [east, north]], {
      padding, maxZoom: region.level === "district" ? 13 : region.level === "city" ? 10 : 8,
      duration: immediate ? 0 : 550,
    });
  }

  setAttractionFilter(attractions: Attraction[]): void {
    if (!this.initialized) return;
    this.filteredAttractions = attractions;
    if (this.selectedAttraction && !attractions.some((item) => item.id === this.selectedAttraction)) {
      this.clearAttractionSelection();
    }
    this.updateAttractionVisibility(true);
  }

  private updateAttractionVisibility(force = false): void {
    if (!this.initialized) return;
    const minimumLevel = minimumAttractionLevel(this.map.getZoom());
    const changed = minimumLevel !== this.displayedMinimumLevel;
    // 平移和同一档内缩放不重新筛选，只有跨档、筛选变化或选中目标变化才更新图层。
    if (!force && !changed) return;
    this.displayedMinimumLevel = minimumLevel;
    const filter: FilterSpecification = ["all",
      ["in", ["get", "id"], ["literal", this.filteredAttractions.map((item) => item.id)]],
      ["any", [">=", ["get", "level"], minimumLevel],
        ["==", ["get", "id"], this.selectedAttraction ?? ""]],
    ];
    this.map.setFilter("attraction-points", filter);
    const { primaryIds, nearbyIds } = attractionLabelScope(this.filteredAttractions, this.selectedRegion);
    this.map.setFilter("attraction-labels", ["all", filter,
      ["in", ["get", "id"], ["literal", [...primaryIds, ...nearbyIds]]],
    ]);
    this.map.setLayoutProperty("attraction-labels", "symbol-sort-key", ["+",
      ["case", ["in", ["get", "id"], ["literal", primaryIds]], 0, 100],
      ["-", 9, ["get", "level"]],
    ]);
    this.map.setPaintProperty("attraction-labels", "text-color", ["case",
      ["in", ["get", "id"], ["literal", primaryIds]], "#e6eee8", "#9fb5aa",
    ]);
    this.popup.remove();
    this.clearHover();
    if (changed) this.callbacks.onAttractionLevelChange(minimumLevel);
  }

  selectAttraction(attraction: Attraction): void {
    this.clearAttractionSelection();
    this.popup.remove();
    this.selectedAttraction = attraction.id;
    this.map.setFeatureState({ source: "attractions", id: attraction.id }, { selected: true });
    this.updateAttractionVisibility(true);
    const content = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = attraction.name;
    content.append(title);
    this.selectedPopup.setDOMContent(content).setLngLat([attraction.lng, attraction.lat]).addTo(this.map);
  }

  private clearAttractionSelection(): void {
    if (this.selectedAttraction) {
      this.map.removeFeatureState({ source: "attractions", id: this.selectedAttraction }, "selected");
    }
    this.selectedAttraction = null;
    this.selectedPopup.remove();
  }

  private featuresAt(point: { x: number; y: number }): MapGeoJSONFeature[] {
    return this.map.queryRenderedFeatures([point.x, point.y], {
      layers: ["attraction-points", "attraction-labels", ...LEVELS.map((level) => level + "-fill")],
    });
  }

  private clearHover(): void {
    if (this.hovered) this.map.removeFeatureState(this.hovered, "hover");
    this.hovered = null;
    this.map.getCanvas().style.cursor = "";
  }

  destroy(): void {
    cancelAnimationFrame(this.resizeFrame);
    this.resizeObserver.disconnect();
    this.popup.remove();
    this.selectedPopup.remove();
    this.map.remove();
  }
}
