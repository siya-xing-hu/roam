import * as echarts from "echarts/core";
import { GeoComponent, TooltipComponent } from "echarts/components";
import { EffectScatterChart, ScatterChart } from "echarts/charts";
import { CanvasRenderer } from "echarts/renderers";
import type { ECharts, EChartsCoreOption } from "echarts/core";
import { LEVEL_COLORS } from "../config";
import type { Attraction, BoundaryCollection, BoundaryFeatureProperties } from "../types";

echarts.use([GeoComponent, TooltipComponent, ScatterChart, EffectScatterChart, CanvasRenderer]);

interface MapCallbacks {
  onRegionClick: (properties: BoundaryFeatureProperties) => void;
  onAttractionClick: (attraction: Attraction) => void;
}

function symbolSize(level: number): number {
  return Math.max(7, 6 + level * 1.6);
}

function escapeHtml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

export class MapView {
  private readonly chart: ECharts;
  private readonly callbacks: MapCallbacks;
  private resizeObserver: ResizeObserver;
  private mapName = "roam-root";
  private regionsByName = new Map<string, BoundaryFeatureProperties>();

  constructor(container: HTMLElement, callbacks: MapCallbacks) {
    this.chart = echarts.init(container, undefined, { renderer: "canvas" });
    this.callbacks = callbacks;
    this.resizeObserver = new ResizeObserver(() => this.chart.resize());
    this.resizeObserver.observe(container);

    this.chart.on("click", (params) => {
      if (params.componentType === "geo" && params.name) {
        const properties = this.regionsByName.get(params.name)
          ?? (params.data as BoundaryFeatureProperties | undefined);
        if (properties) {
          this.callbacks.onRegionClick(properties);
        }
        return;
      }

      if (params.seriesType === "effectScatter" && params.data) {
        const payload = params.data as { attraction?: Attraction };
        if (payload.attraction) {
          this.callbacks.onAttractionClick(payload.attraction);
        }
      }
    });
  }

  render(
    mapData: BoundaryCollection,
    attractions: Attraction[],
    mapName: string,
  ): void {
    this.mapName = mapName;
    this.regionsByName = new Map(
      mapData.features.map((feature) => [feature.properties.name, feature.properties]),
    );
    echarts.registerMap(mapName, mapData as never);

    const options: EChartsCoreOption = {
      animationDurationUpdate: 360,
      backgroundColor: "transparent",
      tooltip: {
        trigger: "item",
        confine: true,
        borderWidth: 0,
        backgroundColor: "rgba(5, 19, 16, 0.94)",
        textStyle: { color: "#f2f7f4", fontSize: 13 },
        extraCssText: "box-shadow: 0 12px 32px rgba(0,0,0,.28); border-radius: 10px;",
        formatter: (params: unknown) => {
          const value = params as {
            seriesType?: string;
            name?: string;
            data?: { attraction?: Attraction };
          };
          if (value.data?.attraction) {
            const attraction = value.data.attraction;
            return `<strong>${escapeHtml(attraction.name)}</strong><br/>等级 ${attraction.level} · ${escapeHtml(attraction.tags.join(" / "))}`;
          }
          return escapeHtml(value.name ?? "");
        },
      },
      geo: {
        map: mapName,
        roam: true,
        scaleLimit: { min: 0.7, max: 14 },
        layoutCenter: ["49%", "52%"],
        layoutSize: "94%",
        selectedMode: false,
        label: {
          show: true,
          color: "#aabdb5",
          fontSize: 11,
          fontFamily: "system-ui, sans-serif",
        },
        itemStyle: {
          areaColor: "#102823",
          borderColor: "#42665c",
          borderWidth: 0.8,
        },
        emphasis: {
          label: { color: "#ffffff", fontWeight: "bold" },
          itemStyle: { areaColor: "#1b453b", borderColor: "#7ec7af", borderWidth: 1.4 },
        },
      },
      series: [
        {
          name: "景点",
          type: "effectScatter",
          coordinateSystem: "geo",
          showEffectOn: "emphasis",
          rippleEffect: { scale: 2.5, brushType: "stroke" },
          zlevel: 3,
          data: attractions.map((attraction) => ({
            name: attraction.name,
            value: [attraction.lng, attraction.lat, attraction.level],
            attraction,
            symbolSize: symbolSize(attraction.level),
            itemStyle: {
              color: LEVEL_COLORS[attraction.level],
              shadowBlur: attraction.level >= 5 ? 12 : 5,
              shadowColor: LEVEL_COLORS[attraction.level],
              borderColor: "rgba(255,255,255,.84)",
              borderWidth: 1,
            },
          })),
        },
      ],
    };

    this.chart.setOption(options, true);
  }

  showLoading(): void {
    this.chart.showLoading("default", {
      text: "加载行政边界…",
      color: "#65d1ae",
      textColor: "#d8e8e2",
      maskColor: "rgba(7, 18, 16, 0.72)",
    });
  }

  hideLoading(): void {
    this.chart.hideLoading();
  }

  resetView(): void {
    this.chart.dispatchAction({ type: "geoRoam", componentIndex: 0, zoom: 1 });
    this.chart.setOption({ geo: { map: this.mapName, zoom: 1, center: undefined } });
  }

  destroy(): void {
    this.resizeObserver.disconnect();
    this.chart.dispose();
  }
}
