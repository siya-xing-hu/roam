import "./styles/main.css";
import { ROOT_REGION } from "./config";
import { AttractionStore, filterAttractions, visibleAttractions } from "./data/attractionStore";
import { BoundaryRepository } from "./data/boundaryRepository";
import { MapView } from "./map/mapView";
import { ViewportBoundaries } from "./map/viewportBoundaries";
import type { Attraction, AttractionFilters, RegionTrailItem } from "./types";
import { AppShell } from "./ui/appShell";

const app = document.querySelector<HTMLElement>("#app");
if (!app) throw new Error("应用根节点不存在");

const boundaries = new BoundaryRepository();
const attractionStore = new AttractionStore();
let trail: RegionTrailItem[] = [];
let allAttractions: Attraction[] = [];
let filteredListAttractions: Attraction[] = [];
let filters: AttractionFilters = { levels: null, query: "" };
let mapView: MapView | null = null;
let viewportBoundaries: ViewportBoundaries | null = null;
let initializing = false;
let renderError: string | null = null;
let selectionRevision = 0;

const shell = new AppShell(app, {
  onBack: () => navigateTo(trail.length - 2),
  onHome: () => selectRegion(ROOT_REGION.adcode),
  onNavigate: navigateTo,
  onAttractionSelect: showAttraction,
  onFiltersChange: (value) => {
    selectionRevision += 1;
    filters = value;
    applyFilters();
  },
  onRetry: () => {
    if (viewportBoundaries) {
      shell.clearStatus();
      viewportBoundaries.retry();
    } else {
      void initialize();
    }
  },
});

async function showAttraction(attraction: Attraction): Promise<void> {
  const revision = ++selectionRevision;
  try {
    const region = await boundaries.resolveRegion(attraction.adcode);
    // 连续点击景点或返回上一级时，过期请求不能把用户带回旧区域。
    if (revision !== selectionRevision || !mapView) return;
    if (!region) throw new Error("尚未找到该景点所属的行政区域");
    trail = boundaries.getTrail(region.adcode);
    updateList();
    shell.showDetails(attraction);
    mapView.focusRegion(region, shell.getMapPadding());
    mapView.selectAttraction(attraction);
  } catch (error) {
    if (revision !== selectionRevision) return;
    shell.showStatus((error instanceof Error ? error.message : "景点所属区域加载失败")
      + "；请重新点击景点重试", "error", false);
  }
}

function updateList(): void {
  const current = trail.at(-1);
  if (current) shell.updateRegion(trail, visibleAttractions(filteredListAttractions, current));
}

function applyFilters(): void {
  // 等级同时控制地图和列表；关键词只查列表，保留地图上的周边景点。
  const mapAttractions = filterAttractions(allAttractions, { levels: filters.levels, query: "" });
  filteredListAttractions = filterAttractions(mapAttractions, filters);
  mapView?.setAttractionFilter(mapAttractions);
  updateList();
}

function selectRegion(adcode: string, immediate = false): void {
  selectionRevision += 1;
  const region = boundaries.getRegion(adcode);
  if (!region || !mapView) return;
  trail = boundaries.getTrail(adcode);
  updateList();
  mapView.focusRegion(region, shell.getMapPadding(), immediate);
}

function navigateTo(index: number): void {
  const region = trail[index];
  if (region) selectRegion(region.adcode);
}

async function initialize(): Promise<void> {
  if (initializing) return;
  initializing = true;
  renderError = null;
  shell.showStatus("正在加载地图和景点…", "loading");
  try {
    if (!mapView) {
      mapView = new MapView(shell.mapElement, {
        onRegionClick: selectRegion,
        onAttractionClick: showAttraction,
        onViewportChange: (viewport) => viewportBoundaries?.update(viewport),
        onAttractionLevelChange: (minimumLevel) => shell.updateMapLevel(minimumLevel),
        onError: (message) => {
          renderError = message;
          shell.showStatus(message, "error", false);
        },
      });
    }
    const [root, dataset] = await Promise.all([
      boundaries.loadRoot(), attractionStore.load(), mapView.ready,
    ]);
    allAttractions = dataset.items;
    shell.setDataset(dataset);
    mapView.initialize(root, allAttractions);
    applyFilters();
    viewportBoundaries = new ViewportBoundaries(boundaries, mapView, root, (error) => {
      if (renderError) return;
      if (error) shell.showStatus(error, "error");
      else shell.clearStatus();
    });
    selectRegion(ROOT_REGION.adcode, true);
    viewportBoundaries.update(mapView.getViewport());
    if (!renderError) shell.clearStatus();
  } catch (error) {
    viewportBoundaries?.destroy();
    viewportBoundaries = null;
    mapView?.destroy();
    mapView = null;
    shell.showStatus(error instanceof Error ? error.message : "地图初始化失败，请检查浏览器是否支持 WebGL", "error");
  } finally {
    initializing = false;
  }
}

void initialize();

if (import.meta.hot) {
  import.meta.hot.dispose(() => {
    selectionRevision += 1;
    viewportBoundaries?.destroy();
    mapView?.destroy();
  });
}
