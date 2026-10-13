import { APP_NAME, LEVEL_COLORS, LEVEL_LABELS } from "../config";
import { belongsToRegion, boundaryLevelLabel } from "../data/attractionStore";
import type { Attraction, AttractionDataset, AttractionFilters, RegionTrailItem } from "../types";

interface ShellCallbacks {
  onBack: () => void;
  onHome: () => void;
  onNavigate: (index: number) => void;
  onAttractionSelect: (attraction: Attraction) => void;
  onFiltersChange: (filters: AttractionFilters) => void;
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
  private filters: AttractionFilters = { levels: null, query: "" };

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
            <p id="boundary-level">全国 · 连续地图</p>
          </div>
          <div class="topbar-actions">
            <button id="home-button" class="icon-button" type="button" aria-label="返回全国" title="返回全国">⌂</button>
            <button id="back-button" class="icon-button" type="button" aria-label="返回上一级" title="返回上一级" disabled>←</button>
          </div>
        </header>

        <nav id="breadcrumb" class="breadcrumb surface" aria-label="行政区域路径"></nav>

        <section class="legend surface" aria-label="景点等级筛选">
          <div class="legend-heading">
            <span>景点等级</span>
            <span id="visible-count" class="count-pill">0 个</span>
          </div>
          <div class="level-filters" data-level-filters></div>
          <p id="threshold-note" class="legend-note"></p>
        </section>

        <aside id="panel" class="side-panel surface" aria-label="当前区域景点">
          <div class="panel-header">
            <div>
              <p class="eyebrow">所选区域</p>
              <h2 id="region-name">全国</h2>
            </div>
            <button id="panel-toggle" class="icon-button panel-toggle" type="button" aria-label="收起景点列表">×</button>
          </div>
          <div id="coverage-note" class="coverage-note"></div>
          <div class="panel-filters">
            <label class="search-label" for="attraction-search">搜索列表中的景点或地区</label>
            <div class="search-field">
              <input id="attraction-search" type="search" placeholder="景点名称、省市区或地址" autocomplete="off" />
              <button id="clear-search" type="button" aria-label="清空搜索" hidden>×</button>
            </div>
            <div class="level-filters panel-level-filters" data-level-filters aria-label="列表等级筛选"></div>
            <p id="filter-summary" class="filter-summary" aria-live="polite"></p>
            <p id="map-level-note" class="filter-summary" aria-live="polite"></p>
          </div>
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
    this.app.querySelector("#breadcrumb")?.addEventListener("click", (event) => {
      const button = (event.target as HTMLElement).closest<HTMLButtonElement>("button[data-trail-index]");
      if (button) callbacks.onNavigate(Number(button.dataset.trailIndex));
    });
    this.app.querySelector("#panel-toggle")?.addEventListener("click", () => this.setPanelOpen(false));
    this.app.querySelector("#panel-open")?.addEventListener("click", () => this.setPanelOpen(true));
    this.app.addEventListener("click", (event) => {
      const button = (event.target as HTMLElement).closest<HTMLButtonElement>("button[data-filter-level]");
      if (!button) return;
      const value = button.dataset.filterLevel;
      if (value === "all") this.filters.levels = null;
      else {
        const level = Number(value);
        const selected = new Set(this.filters.levels ?? []);
        if (selected.has(level)) selected.delete(level);
        else selected.add(level);
        this.filters.levels = [...selected];
      }
      this.emitFilters();
    });
    const search = this.app.querySelector<HTMLInputElement>("#attraction-search")!;
    search.addEventListener("input", () => {
      this.filters.query = search.value;
      this.emitFilters();
    });
    this.app.querySelector("#clear-search")?.addEventListener("click", () => {
      search.value = "";
      this.filters.query = "";
      this.emitFilters();
      search.focus();
    });
  }

  setDataset(dataset: AttractionDataset): void {
    this.dataset = dataset;
    const levels = [...new Set(dataset.items.map((item) => item.level))].sort((a, b) => b - a);
    const controls = `<button type="button" class="level-filter" data-filter-level="all" aria-pressed="true">全部</button>`
      + levels.map((level) => `<button type="button" class="level-filter" data-filter-level="${level}"
        style="--level-color:${LEVEL_COLORS[level]}" aria-pressed="false"><i aria-hidden="true"></i>${LEVEL_LABELS[level]}</button>`).join("");
    this.app.querySelectorAll<HTMLElement>("[data-level-filters]").forEach((group) => { group.innerHTML = controls; });
    this.updateFilterControls();
  }

  updateRegion(trail: RegionTrailItem[], attractions: Attraction[]): void {
    const current = trail.at(-1);
    if (!current) {
      return;
    }

    const regionName = this.app.querySelector<HTMLElement>("#region-name");
    const boundaryLevel = this.app.querySelector<HTMLElement>("#boundary-level");
    const visibleCount = this.app.querySelector<HTMLElement>("#visible-count");
    const backButton = this.app.querySelector<HTMLButtonElement>("#back-button");

    if (regionName) regionName.textContent = current.name;
    if (boundaryLevel) boundaryLevel.textContent = `${current.name} · ${boundaryLevelLabel(current.level)}`;
    if (visibleCount) visibleCount.textContent = `列表 ${attractions.length} 个`;
    const summary = this.app.querySelector<HTMLElement>("#filter-summary");
    if (summary) summary.textContent = `当前列表 ${attractions.length} 处 · 点击进入景点所在区域`;
    if (backButton) backButton.disabled = trail.length <= 1;

    this.renderBreadcrumb(trail);
    this.renderCoverage(current);
    this.renderAttractions(attractions);
    this.hideDetails();
  }

  updateMapLevel(minimumLevel: number): void {
    const message = minimumLevel > 0
      ? `缩放门槛：${minimumLevel} 级及以上 · 放大可见更多`
      : "缩放门槛：全部等级（含未评级）";
    for (const selector of ["#threshold-note", "#map-level-note"]) {
      const note = this.app.querySelector<HTMLElement>(selector);
      if (!note) continue;
      note.textContent = message;
      note.title = "与手动等级筛选共同生效；主动选中的景点保留定位标记";
    }
  }

  getMapPadding(): { top: number; bottom: number; left: number; right: number } {
    const canvas = this.mapElement.getBoundingClientRect();
    const panel = this.app.querySelector<HTMLElement>("#panel");
    const open = panel && !panel.classList.contains("is-closed");
    if (canvas.width <= 860) {
      // 使用面板最终尺寸，避免开合动画中读到旧位置，把景点定位到面板下面。
      const bottom = open ? panel.offsetHeight + 25 : 150;
      return { top: Math.min(145, canvas.height * 0.25), left: 25, right: 25,
        bottom: Math.min(bottom, canvas.height * 0.62) };
    }
    return { top: 150, bottom: 70, left: 45, right: open ? 435 : 45 };
  }

  showDetails(attraction: Attraction): void {
    const details = this.app.querySelector<HTMLElement>("#details");
    const list = this.app.querySelector<HTMLElement>("#attraction-list");
    if (!details || !list) return;

    details.hidden = false;
    list.hidden = true;
    this.app.querySelectorAll<HTMLElement>(".attraction-card").forEach((card) => {
      card.setAttribute("aria-pressed", String(card.dataset.id === attraction.id));
    });
    details.innerHTML = `
      <button class="details-back" type="button">← 返回列表</button>
      <div class="details-level" style="--level-color:${LEVEL_COLORS[attraction.level]}">
        <span>${LEVEL_LABELS[attraction.level]}</span>
        <small>地图等级</small>
      </div>
      <h3>${escapeHtml(attraction.name)}</h3>
      <div class="tag-row">${attraction.tags.map((tag) => `<span>${escapeHtml(tag)}</span>`).join("")}</div>
      <dl>
        <div><dt>行政区域</dt><dd>${escapeHtml([attraction.province, attraction.city, attraction.district].filter(Boolean).join(" · "))}</dd></div>
        <div><dt>地址</dt><dd>${escapeHtml(attraction.address)}</dd></div>
        <div><dt>等级来源</dt><dd>${attraction.levelSource === "official" ? "官方 A 级映射" : "人工精选"}</dd></div>
        <div><dt>数据来源</dt><dd><a href="${escapeHtml(attraction.source.url)}" target="_blank" rel="noreferrer">${escapeHtml(attraction.source.name)}</a></dd></div>
        ${attraction.coordinateSource ? `<div><dt>坐标来源</dt><dd><a href="${escapeHtml(attraction.coordinateSource.url)}" target="_blank" rel="noreferrer">${escapeHtml(attraction.coordinateSource.name)}</a> · ${attraction.coordinateSource.method === "point-on-surface" ? "景区范围内代表点" : "地图标注点"}</dd></div>` : ""}
      </dl>
    `;
    details.querySelector(".details-back")?.addEventListener("click", () => this.hideDetails());
    this.setPanelOpen(true);
  }

  showStatus(message: string, kind: "loading" | "error" | "info" = "info", retry = kind === "error"): void {
    const status = this.app.querySelector<HTMLElement>("#status");
    if (!status) return;
    status.className = `status-message is-visible status-${kind}`;
    status.innerHTML = retry
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

  private updateFilterControls(): void {
    this.app.querySelectorAll<HTMLButtonElement>("[data-filter-level]").forEach((button) => {
      const value = button.dataset.filterLevel;
      button.setAttribute("aria-pressed", String(value === "all"
        ? this.filters.levels === null : this.filters.levels?.includes(Number(value)) ?? false));
    });
    const clear = this.app.querySelector<HTMLButtonElement>("#clear-search");
    if (clear) clear.hidden = !this.filters.query;
  }

  private emitFilters(): void {
    this.updateFilterControls();
    this.callbacks.onFiltersChange({ ...this.filters,
      levels: this.filters.levels === null ? null : [...this.filters.levels] });
  }

  private renderBreadcrumb(trail: RegionTrailItem[]): void {
    const breadcrumb = this.app.querySelector<HTMLElement>("#breadcrumb");
    if (!breadcrumb) return;
    breadcrumb.innerHTML = trail
      .map(
        (item, index) => `
          ${index > 0 ? '<span aria-hidden="true">/</span>' : ""}
          <button type="button" data-trail-index="${index}"
            ${index === trail.length - 1 ? 'aria-current="page"' : ""}
            title="${index === trail.length - 1 ? "恢复" : "返回"}${escapeHtml(item.name)}完整范围">${escapeHtml(item.name)}</button>`,
      )
      .join("");
  }

  private renderCoverage(region: RegionTrailItem): void {
    const note = this.app.querySelector<HTMLElement>("#coverage-note");
    if (!note || !this.dataset) return;

    const count = this.dataset.items.filter((item) => belongsToRegion(item, region)).length;
    const covered = count > 0;
    note.className = `coverage-note ${covered ? "is-covered" : "is-uncovered"}`;
    note.textContent = covered
      ? `已收录${region.name} ${count} 处景点；拖动地图不改变此列表`
      : `${region.name}的景点数据尚未收录`;
  }

  private renderAttractions(attractions: Attraction[]): void {
    const list = this.app.querySelector<HTMLElement>("#attraction-list");
    if (!list) return;
    list.hidden = false;

    if (attractions.length === 0) {
      list.innerHTML = '<div class="empty-state"><strong>没有符合筛选条件的景点</strong><span>请选择其他等级、修改搜索词，或点击全国／省市区切换列表范围。</span></div>';
      return;
    }

    list.innerHTML = attractions
      .map(
        (attraction) => `
          <button class="attraction-card" type="button" data-id="${escapeHtml(attraction.id)}" aria-pressed="false">
            <span class="level-badge" style="--level-color:${LEVEL_COLORS[attraction.level]}">${LEVEL_LABELS[attraction.level]}</span>
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
