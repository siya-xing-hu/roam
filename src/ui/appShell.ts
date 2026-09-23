import { APP_NAME, DISPLAY_THRESHOLD, LEVEL_COLORS, LEVEL_LABELS } from "../config";
import { boundaryLevelLabel } from "../data/attractionStore";
import type { Attraction, AttractionDataset, RegionTrailItem } from "../types";

interface ShellCallbacks {
  onBack: () => void;
  onHome: () => void;
  onAttractionSelect: (attraction: Attraction) => void;
  onRetry: () => void;
}

function escapeHtml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

export class AppShell {
  readonly mapElement: HTMLElement;
  private readonly app: HTMLElement;
  private readonly callbacks: ShellCallbacks;
  private dataset: AttractionDataset | null = null;

  constructor(app: HTMLElement, callbacks: ShellCallbacks) {
    this.app = app;
    this.callbacks = callbacks;
    this.app.innerHTML = `
      <main class="map-app" aria-label="${APP_NAME}">
        <div id="map" class="map-canvas" role="application" aria-label="全国行政区划与景点地图"></div>
        <header class="topbar surface">
          <div class="brand-mark" aria-hidden="true">旅</div>
          <div class="brand-copy">
            <h1>${APP_NAME}</h1>
            <p id="boundary-level">全国 · 省级边界</p>
          </div>
          <div class="topbar-actions">
            <button id="home-button" class="icon-button" type="button" aria-label="返回全国" title="返回全国">⌂</button>
            <button id="back-button" class="icon-button" type="button" aria-label="返回上一级" title="返回上一级" disabled>←</button>
          </div>
        </header>

        <nav id="breadcrumb" class="breadcrumb surface" aria-label="行政区域路径"></nav>

        <section class="legend surface" aria-label="地图图例">
          <div class="legend-heading">
            <span>景点等级</span>
            <span id="visible-count" class="count-pill">0 个</span>
          </div>
          <div class="legend-scale">
            ${[9, 8, 7, 6, 5, 4, 3, 2, 1, 0]
              .map(
                (level) => `
                  <span class="legend-level" title="${LEVEL_LABELS[level]}">
                    <i style="--level-color:${LEVEL_COLORS[level]}"></i>${level}
                  </span>`,
              )
              .join("")}
          </div>
          <p id="threshold-note" class="legend-note"></p>
        </section>

        <aside id="panel" class="side-panel surface" aria-label="当前区域景点">
          <div class="panel-header">
            <div>
              <p class="eyebrow">当前区域</p>
              <h2 id="region-name">全国</h2>
            </div>
            <button id="panel-toggle" class="icon-button panel-toggle" type="button" aria-label="收起景点列表">×</button>
          </div>
          <div id="coverage-note" class="coverage-note"></div>
          <div id="attraction-list" class="attraction-list"></div>
          <div id="details" class="details" hidden></div>
        </aside>

        <button id="panel-open" class="panel-open" type="button" hidden>查看景点</button>
        <div id="status" class="status-message" role="status" aria-live="polite"></div>
      </main>
    `;

    const mapElement = this.app.querySelector<HTMLElement>("#map");
    if (!mapElement) {
      throw new Error("地图容器初始化失败");
    }
    this.mapElement = mapElement;

    this.app.querySelector("#back-button")?.addEventListener("click", callbacks.onBack);
    this.app.querySelector("#home-button")?.addEventListener("click", callbacks.onHome);
    this.app.querySelector("#panel-toggle")?.addEventListener("click", () => this.setPanelOpen(false));
    this.app.querySelector("#panel-open")?.addEventListener("click", () => this.setPanelOpen(true));
  }

  setDataset(dataset: AttractionDataset): void {
    this.dataset = dataset;
  }

  updateRegion(trail: RegionTrailItem[], attractions: Attraction[]): void {
    const current = trail.at(-1);
    if (!current) {
      return;
    }

    const regionName = this.app.querySelector<HTMLElement>("#region-name");
    const boundaryLevel = this.app.querySelector<HTMLElement>("#boundary-level");
    const visibleCount = this.app.querySelector<HTMLElement>("#visible-count");
    const thresholdNote = this.app.querySelector<HTMLElement>("#threshold-note");
    const backButton = this.app.querySelector<HTMLButtonElement>("#back-button");

    if (regionName) regionName.textContent = current.name;
    if (boundaryLevel) boundaryLevel.textContent = `${current.name} · ${boundaryLevelLabel(current.level)}`;
    if (visibleCount) visibleCount.textContent = `${attractions.length} 个`;
    if (thresholdNote) {
      const threshold = DISPLAY_THRESHOLD[current.level];
      thresholdNote.textContent = threshold > 0 ? `当前显示 ${threshold} 级及以上景点` : "当前显示全部等级";
    }
    if (backButton) backButton.disabled = trail.length <= 1;

    this.renderBreadcrumb(trail);
    this.renderCoverage(current.name, current.adcode);
    this.renderAttractions(attractions);
    this.hideDetails();
  }

  showDetails(attraction: Attraction): void {
    const details = this.app.querySelector<HTMLElement>("#details");
    const list = this.app.querySelector<HTMLElement>("#attraction-list");
    if (!details || !list) return;

    details.hidden = false;
    list.hidden = true;
    details.innerHTML = `
      <button class="details-back" type="button">← 返回列表</button>
      <div class="details-level" style="--level-color:${LEVEL_COLORS[attraction.level]}">
        <span>${attraction.level}</span>
        <small>地图等级</small>
      </div>
      <h3>${escapeHtml(attraction.name)}</h3>
      <div class="tag-row">${attraction.tags.map((tag) => `<span>${escapeHtml(tag)}</span>`).join("")}</div>
      <dl>
        <div><dt>行政区域</dt><dd>${escapeHtml([attraction.province, attraction.city, attraction.district].filter(Boolean).join(" · "))}</dd></div>
        <div><dt>地址</dt><dd>${escapeHtml(attraction.address)}</dd></div>
        <div><dt>等级来源</dt><dd>${attraction.levelSource === "official" ? "官方 A 级映射" : "人工精选"}</dd></div>
        <div><dt>数据来源</dt><dd><a href="${escapeHtml(attraction.source.url)}" target="_blank" rel="noreferrer">${escapeHtml(attraction.source.name)}</a></dd></div>
      </dl>
    `;
    details.querySelector(".details-back")?.addEventListener("click", () => this.hideDetails());
    this.setPanelOpen(true);
  }

  showStatus(message: string, kind: "loading" | "error" | "info" = "info"): void {
    const status = this.app.querySelector<HTMLElement>("#status");
    if (!status) return;
    status.className = `status-message is-visible status-${kind}`;
    status.innerHTML = kind === "error"
      ? `<span>${escapeHtml(message)}</span><button type="button">重试</button>`
      : escapeHtml(message);
    status.querySelector("button")?.addEventListener("click", this.callbacks.onRetry);
  }

  clearStatus(): void {
    const status = this.app.querySelector<HTMLElement>("#status");
    if (!status) return;
    status.className = "status-message";
    status.textContent = "";
  }

  private renderBreadcrumb(trail: RegionTrailItem[]): void {
    const breadcrumb = this.app.querySelector<HTMLElement>("#breadcrumb");
    if (!breadcrumb) return;
    breadcrumb.innerHTML = trail
      .map(
        (item, index) => `
          ${index > 0 ? '<span aria-hidden="true">/</span>' : ""}
          <span ${index === trail.length - 1 ? 'aria-current="page"' : ""}>${escapeHtml(item.name)}</span>`,
      )
      .join("");
  }

  private renderCoverage(regionName: string, adcode: string): void {
    const note = this.app.querySelector<HTMLElement>("#coverage-note");
    if (!note || !this.dataset) return;

    const covered = adcode === "100000" || ["31", "32", "33"].includes(adcode.slice(0, 2));
    note.className = `coverage-note ${covered ? "is-covered" : "is-uncovered"}`;
    note.textContent = covered
      ? `已加载${this.dataset.coverage.join("、")}官方 A 级景区数据`
      : `${regionName}的景点数据尚未收录`;
  }

  private renderAttractions(attractions: Attraction[]): void {
    const list = this.app.querySelector<HTMLElement>("#attraction-list");
    if (!list) return;
    list.hidden = false;

    if (attractions.length === 0) {
      list.innerHTML = '<div class="empty-state"><strong>当前没有可显示的景点</strong><span>尝试进入江浙沪地区或放大地图。</span></div>';
      return;
    }

    list.innerHTML = attractions
      .map(
        (attraction) => `
          <button class="attraction-card" type="button" data-id="${escapeHtml(attraction.id)}">
            <span class="level-badge" style="--level-color:${LEVEL_COLORS[attraction.level]}">${attraction.level}</span>
            <span class="attraction-copy">
              <strong>${escapeHtml(attraction.name)}</strong>
              <small>${escapeHtml(attraction.district || attraction.city)} · ${escapeHtml(attraction.tags.join(" / ") || "未评级")}</small>
            </span>
            <span class="card-arrow" aria-hidden="true">›</span>
          </button>`,
      )
      .join("");

    list.querySelectorAll<HTMLButtonElement>(".attraction-card").forEach((button) => {
      button.addEventListener("click", () => {
        const attraction = attractions.find((item) => item.id === button.dataset.id);
        if (attraction) this.callbacks.onAttractionSelect(attraction);
      });
    });
  }

  private hideDetails(): void {
    const details = this.app.querySelector<HTMLElement>("#details");
    const list = this.app.querySelector<HTMLElement>("#attraction-list");
    if (details) details.hidden = true;
    if (list) list.hidden = false;
  }

  private setPanelOpen(open: boolean): void {
    const panel = this.app.querySelector<HTMLElement>("#panel");
    const trigger = this.app.querySelector<HTMLButtonElement>("#panel-open");
    panel?.classList.toggle("is-closed", !open);
    if (trigger) trigger.hidden = open;
  }
}
