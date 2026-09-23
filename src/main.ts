import "./styles/main.css";
import { nextBoundaryLevel, ROOT_REGION } from "./config";
import { AttractionStore, visibleAttractions } from "./data/attractionStore";
import { BoundaryRepository } from "./data/boundaryRepository";
import { MapView } from "./map/mapView";
import type {
  Attraction,
  BoundaryFeatureProperties,
  BoundaryLevel,
  RegionTrailItem,
} from "./types";
import { AppShell } from "./ui/appShell";

const app = document.querySelector<HTMLElement>("#app");
if (!app) {
  throw new Error("应用根节点不存在");
}

const boundaries = new BoundaryRepository();
const attractionStore = new AttractionStore();
let trail: RegionTrailItem[] = [];
let allAttractions: Attraction[] = [];
let currentLoadToken = 0;

const shell = new AppShell(app, {
  onBack: () => void navigateBack(),
  onHome: () => void navigateHome(),
  onAttractionSelect: (attraction) => shell.showDetails(attraction),
  onRetry: () => void initialize(),
});

const mapView = new MapView(shell.mapElement, {
  onRegionClick: (properties) => void drillDown(properties),
  onAttractionClick: (attraction) => shell.showDetails(attraction),
});

function renderCurrent(): void {
  const current = trail.at(-1);
  if (!current) return;
  const attractions = visibleAttractions(allAttractions, current);
  mapView.render(current.mapData, attractions, `roam-${current.adcode}-${current.level}`);
  shell.updateRegion(trail, attractions);
}

async function initialize(): Promise<void> {
  const token = ++currentLoadToken;
  shell.showStatus("正在加载全国行政边界…", "loading");
  mapView.showLoading();

  try {
    const [rootMap, dataset] = await Promise.all([
      boundaries.loadRoot(),
      attractionStore.load(),
    ]);
    if (token !== currentLoadToken) return;

    allAttractions = dataset.items;
    shell.setDataset(dataset);
    trail = [{ ...ROOT_REGION, mapData: rootMap }];
    renderCurrent();
    shell.clearStatus();
  } catch (error) {
    const message = error instanceof Error ? error.message : "地图加载失败";
    shell.showStatus(message, "error");
  } finally {
    mapView.hideLoading();
  }
}

async function drillDown(properties: BoundaryFeatureProperties): Promise<void> {
  const current = trail.at(-1);
  if (!current) return;

  const adcode = String(properties.adcode).padStart(6, "0");
  if (properties.level === "district" || properties.childrenNum === 0) {
    const selectedFeature = current.mapData.features.find(
      (feature) => String(feature.properties.adcode).padStart(6, "0") === adcode,
    );
    const mapData = selectedFeature
      ? { ...current.mapData, features: [selectedFeature] }
      : current.mapData;
    trail.push({
      adcode,
      name: properties.name,
      level: "district",
      mapData,
    });
    renderCurrent();
    shell.clearStatus();
    return;
  }

  const targetLevel = nextBoundaryLevel(current.level, adcode);
  if (!targetLevel || current.level === "district") {
    return;
  }

  const token = ++currentLoadToken;
  shell.showStatus(`正在加载${properties.name}行政边界…`, "loading");
  mapView.showLoading();

  try {
    const mapData = await boundaries.loadChildren(targetLevel, adcode);
    if (token !== currentLoadToken) return;

    const regionLevel = targetLevel === "district" ? "city" : "province";
    trail.push({
      adcode,
      name: properties.name,
      level: regionLevel as BoundaryLevel,
      mapData,
    });
    renderCurrent();
    shell.clearStatus();
  } catch (error) {
    const message = error instanceof Error ? error.message : "下级边界加载失败";
    shell.showStatus(`${properties.name}：${message}`, "error");
  } finally {
    mapView.hideLoading();
  }
}

async function navigateBack(): Promise<void> {
  if (trail.length <= 1) return;
  currentLoadToken += 1;
  trail.pop();
  renderCurrent();
  shell.clearStatus();
}

async function navigateHome(): Promise<void> {
  if (trail.length <= 1) {
    mapView.resetView();
    return;
  }
  currentLoadToken += 1;
  trail = trail.slice(0, 1);
  renderCurrent();
  shell.clearStatus();
}

void initialize();
