import { createHash } from "node:crypto";
import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const toolRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const projectRoot = resolve(toolRoot, "../..");
const outputPath = resolve(projectRoot, "public/data/attractions.json");
const reportPath = resolve(toolRoot, "generation-report.json");
const cachePath = resolve(toolRoot, "cache/geocodes.json");
const sourcesPath = resolve(toolRoot, "sources.json");
const overridesPath = resolve(toolRoot, "overrides.json");
const amapKey = process.env.AMAP_WEB_SERVICE_KEY;
const gradePattern = /^([1-5])A$/;
const requestHeaders = { "user-agent": "travel-map-personal-data-generator/0.1" };

function decodeHtml(value) {
  return value
    .replace(/<br\s*\/?\s*>/gi, " ")
    .replace(/<[^>]+>/g, "")
    .replaceAll("&nbsp;", " ")
    .replaceAll("&amp;", "&")
    .replaceAll("&lt;", "<")
    .replaceAll("&gt;", ">")
    .replaceAll("&quot;", '"')
    .replace(/&#(\d+);/g, (_, code) => String.fromCodePoint(Number(code)))
    .replace(/\s+/g, " ")
    .trim();
}

function normalizeGrade(value) {
  const normalized = String(value ?? "").toUpperCase().replaceAll("级", "").trim();
  if (/^A{1,5}$/.test(normalized)) return `${normalized.length}A`;
  return normalized;
}

function parseHtmlRows(html, source) {
  const publishedAt = html.match(/(20\d{2})[-年/.](\d{1,2})[-月/.](\d{1,2})/)?.slice(1, 4);
  const publicationDate = publishedAt
    ? publishedAt.map((part, index) => (index === 0 ? part : part.padStart(2, "0"))).join("-")
    : "";
  const rows = [...html.matchAll(/<tr[^>]*>([\s\S]*?)<\/tr>/gi)];
  const attractions = [];

  for (const row of rows) {
    const cells = [...row[1].matchAll(/<t[dh][^>]*>([\s\S]*?)<\/t[dh]>/gi)].map((cell) =>
      decodeHtml(cell[1]),
    );
    if (cells.length < 3 || !/^\d+$/.test(cells[0])) continue;

    const gradeIndex = cells.findIndex((cell) => gradePattern.test(cell));
    const grade = normalizeGrade(source.grade ?? (gradeIndex >= 0 ? cells[gradeIndex] : ""));
    const name = cells[1];
    const address = cells.at(-1) ?? "";
    if (!name || !gradePattern.test(grade) || !address) continue;

    attractions.push({
      name,
      grade,
      address,
      province: source.province,
      sourceName: source.sourceName,
      sourceUrl: source.url,
      publishedAt: publicationDate,
    });
  }

  if (attractions.length === 0) {
    throw new Error(`${source.url} 未解析到景区表格，网页结构可能已经变化`);
  }
  return attractions;
}

function parseJiangsuRows(html, source) {
  const rows = [...html.matchAll(/<li\b[^>]*>([\s\S]*?)<\/li>/gi)];
  const attractions = [];

  for (const row of rows) {
    const cells = [...row[1].matchAll(/<span\b[^>]*>([\s\S]*?)<\/span>/gi)].map((cell) =>
      decodeHtml(cell[1]),
    );
    if (cells.length < 5) continue;

    const name = cells[1];
    const city = cells[2];
    const district = cells[3];
    const grade = normalizeGrade(cells[4]);
    if (!name || !gradePattern.test(grade)) continue;

    attractions.push({
      name,
      grade,
      city,
      district,
      // 江苏公开名录没有门牌地址。保留官方地区和名称组成的检索文本，
      // 供高德地理编码定位，同时避免把第三方门牌信息冒充为官方字段。
      address: `${source.province}${city}${district}${name}`,
      province: source.province,
      sourceName: source.sourceName,
      sourceUrl: source.sourceUrl,
      publishedAt: source.publishedAt,
    });
  }

  return attractions;
}

async function fetchText(url) {
  const response = await fetch(url, { headers: requestHeaders });
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
  return response.text();
}

async function loadJiangsuPortal(source) {
  const buildPageUrl = (page) => {
    const url = new URL(source.searchUrl);
    url.searchParams.set("currpage", String(page));
    return url.toString();
  };
  const firstPage = await fetchText(buildPageUrl(1));
  const pageCount = Number(decodeHtml(firstPage).match(/共\s*(\d+)\s*页/)?.[1] ?? 1);
  if (!Number.isInteger(pageCount) || pageCount < 1 || pageCount > 500) {
    throw new Error(`江苏名录页数异常：${pageCount}`);
  }

  const pages = [firstPage];
  const batchSize = 4;
  for (let start = 2; start <= pageCount; start += batchSize) {
    const pageNumbers = Array.from(
      { length: Math.min(batchSize, pageCount - start + 1) },
      (_, index) => start + index,
    );
    pages.push(...(await Promise.all(pageNumbers.map((page) => fetchText(buildPageUrl(page))))));
  }

  const attractions = pages.flatMap((html) => parseJiangsuRows(html, source));
  if (attractions.length === 0) {
    throw new Error(`${source.sourceUrl} 未解析到景区名录，网页结构可能已经变化`);
  }
  return attractions;
}

function parseCsv(content) {
  const rows = [];
  let row = [];
  let cell = "";
  let quoted = false;
  for (let index = 0; index < content.length; index += 1) {
    const char = content[index];
    const next = content[index + 1];
    if (char === '"' && quoted && next === '"') {
      cell += '"';
      index += 1;
    } else if (char === '"') {
      quoted = !quoted;
    } else if (char === "," && !quoted) {
      row.push(cell.trim());
      cell = "";
    } else if ((char === "\n" || char === "\r") && !quoted) {
      if (char === "\r" && next === "\n") index += 1;
      row.push(cell.trim());
      if (row.some(Boolean)) rows.push(row);
      row = [];
      cell = "";
    } else {
      cell += char;
    }
  }
  row.push(cell.trim());
  if (row.some(Boolean)) rows.push(row);
  if (rows.length < 2) return [];
  const headers = rows[0].map(normalizeHeader);
  return rows.slice(1).map((values) =>
    Object.fromEntries(headers.map((header, index) => [header, values[index] ?? ""])),
  );
}

function normalizeHeader(header) {
  const key = header.trim().toLowerCase();
  const aliases = {
    景区名称: "name",
    景点名称: "name",
    名称: "name",
    unitname: "name",
    等级: "grade",
    景区等级: "grade",
    level: "grade",
    地址: "address",
    景区地址: "address",
    address: "address",
    城市: "city",
    地市: "city",
    city: "city",
    区县: "district",
    district: "district",
    发布日期: "publishedAt",
    publishedat: "publishedAt",
    来源链接: "sourceUrl",
    sourceurl: "sourceUrl",
  };
  return aliases[key] ?? key;
}

function normalizeInputRows(rows, source) {
  return rows.map((inputRow) => {
    const row = Object.fromEntries(
      Object.entries(inputRow).map(([key, value]) => [normalizeHeader(key), value]),
    );
    return {
      name: String(row.name ?? row.unitname ?? "").trim(),
      grade: normalizeGrade(row.grade ?? row.level),
      address: String(row.address ?? "").trim(),
      city: String(row.city ?? "").trim(),
      district: String(row.district ?? "").trim(),
      province: source.province,
      sourceName: source.sourceName,
      sourceUrl: String(row.sourceUrl ?? source.sourceUrl ?? "").trim(),
      publishedAt: String(row.publishedAt ?? source.publishedAt ?? "").trim(),
    };
  });
}

async function loadSource(source) {
  if (source.kind === "html") {
    return parseHtmlRows(await fetchText(source.url), source);
  }
  if (source.kind === "jiangsu-portal") {
    return loadJiangsuPortal(source);
  }
  if (source.kind === "remote-json") {
    const parsed = JSON.parse(await fetchText(source.url));
    const rows = Array.isArray(parsed) ? parsed : parsed.items;
    if (!Array.isArray(rows)) throw new Error(`${source.url} 必须返回数组或包含 items 数组`);
    return normalizeInputRows(rows, source);
  }
  const path = resolve(toolRoot, source.path);
  const content = await readFile(path, "utf8");
  if (source.kind === "csv") {
    return normalizeInputRows(parseCsv(content.replace(/^\uFEFF/, "")), source);
  }
  if (source.kind === "json") {
    const parsed = JSON.parse(content);
    const rows = Array.isArray(parsed) ? parsed : parsed.items;
    if (!Array.isArray(rows)) throw new Error(`${source.path} 必须是数组或包含 items 数组`);
    return normalizeInputRows(rows, source);
  }
  throw new Error(`不支持的数据源类型：${source.kind}`);
}

function validateRaw(item) {
  if (!item.name) return "缺少名称";
  if (!gradePattern.test(item.grade)) return `等级无效：${item.grade}`;
  if (!item.address) return "缺少地址";
  if (!item.sourceUrl) return "缺少来源链接";
  if (!item.publishedAt) return "缺少来源发布日期";
  return "";
}

function stableId(item) {
  const prefix = { 上海市: "sh", 江苏省: "js", 浙江省: "zj" }[item.province] ?? "cn";
  const hash = createHash("sha256").update(`${item.province}|${item.name}`).digest("hex").slice(0, 14);
  return `${prefix}-${hash}`;
}

async function readJson(path, fallback) {
  try {
    return JSON.parse(await readFile(path, "utf8"));
  } catch (error) {
    if (error?.code === "ENOENT") return fallback;
    throw error;
  }
}

async function geocode(item, cache) {
  const key = `${item.province}|${item.city}|${item.address}`;
  if (cache[key]) return cache[key];
  if (!amapKey) throw new Error("缺少 AMAP_WEB_SERVICE_KEY，无法补充经纬度");
  const query = new URLSearchParams({
    key: amapKey,
    address: `${item.province}${item.city}${item.address}`,
    city: item.city || item.province,
  });
  const response = await fetch(`https://restapi.amap.com/v3/geocode/geo?${query}`);
  if (!response.ok) throw new Error(`高德地理编码 HTTP ${response.status}`);
  const data = await response.json();
  const result = data.geocodes?.[0];
  if (data.status !== "1" || !result?.location) {
    throw new Error(`高德未找到地址：${item.name} / ${item.address}`);
  }
  const [lng, lat] = result.location.split(",").map(Number);
  const geocoded = {
    lng,
    lat,
    province: result.province || item.province,
    city: Array.isArray(result.city) ? item.city : result.city || item.city,
    district: result.district || item.district,
    adcode: String(result.adcode || ""),
  };
  cache[key] = geocoded;
  return geocoded;
}

function applyOverride(record, override) {
  if (!override) return record;
  const nextLevel = override.level ?? record.level;
  if (!Number.isInteger(nextLevel) || nextLevel < 0 || nextLevel > 9) {
    throw new Error(`${record.id} 的人工等级必须是 0–9 整数`);
  }
  return {
    ...record,
    level: nextLevel,
    levelSource: override.level === undefined ? record.levelSource : "manual",
    tags: [...new Set([...record.tags, ...(override.tags ?? [])])],
  };
}

function validateRecord(record) {
  if (!record.id || !record.name) return "缺少 ID 或名称";
  if (!/^\d{6}$/.test(record.adcode)) return `行政编码无效：${record.adcode}`;
  if (!Number.isInteger(record.level) || record.level < 0 || record.level > 9) return "等级必须是 0–9 整数";
  if (!Number.isFinite(record.lng) || !Number.isFinite(record.lat)) return "坐标无效";
  if (record.lng < 70 || record.lng > 140 || record.lat < 0 || record.lat > 60) return "坐标超出中国合理范围";
  const officialTag = record.tags.find((tag) => gradePattern.test(tag));
  if (!officialTag) return "缺少官方 A 级标签";
  if (record.levelSource === "official" && Number(officialTag[0]) !== record.level) return "官方等级映射错误";
  return "";
}

async function atomicWrite(path, value) {
  await mkdir(dirname(path), { recursive: true });
  const temporary = `${path}.tmp`;
  await writeFile(temporary, `${JSON.stringify(value, null, 2)}\n`);
  await rename(temporary, path);
}

const sources = await readJson(sourcesPath, []);
const overrides = await readJson(overridesPath, {});
const cache = await readJson(cachePath, {});
const report = {
  generatedAt: new Date().toISOString(),
  sources: [],
  rawCount: 0,
  duplicateCount: 0,
  generatedCount: 0,
  errors: [],
  overrides: [],
};

const rawItems = [];
for (const source of sources) {
  try {
    const items = await loadSource(source);
    rawItems.push(...items);
    report.sources.push({ name: source.sourceName, kind: source.kind, count: items.length });
  } catch (error) {
    report.errors.push({ source: source.sourceName, message: error instanceof Error ? error.message : String(error) });
  }
}
report.rawCount = rawItems.length;

const unique = new Map();
for (const item of rawItems) {
  const problem = validateRaw(item);
  if (problem) {
    report.errors.push({ item: item.name || "未命名", message: problem });
    continue;
  }
  const id = stableId(item);
  if (unique.has(id)) {
    report.duplicateCount += 1;
    continue;
  }
  unique.set(id, { ...item, id });
}

const records = [];
if (!amapKey && unique.size > 0) {
  report.errors.push({
    source: "高德地理编码",
    message: `缺少 AMAP_WEB_SERVICE_KEY，${unique.size} 条景区尚未补充经纬度`,
  });
} else {
  for (const item of unique.values()) {
    try {
      const location = await geocode(item, cache);
      const level = Number(item.grade[0]);
      let record = {
        id: item.id,
        name: item.name,
        province: location.province,
        city: location.city,
        district: location.district,
        adcode: location.adcode,
        address: item.address,
        lng: location.lng,
        lat: location.lat,
        level,
        levelSource: "official",
        tags: [item.grade],
        source: {
          name: item.sourceName,
          url: item.sourceUrl,
          publishedAt: item.publishedAt,
        },
      };
      record = applyOverride(record, overrides[item.id]);
      if (overrides[item.id]) report.overrides.push(item.id);
      const problem = validateRecord(record);
      if (problem) throw new Error(problem);
      records.push(record);
    } catch (error) {
      report.errors.push({ item: item.name, message: error instanceof Error ? error.message : String(error) });
    }
  }
}

records.sort((left, right) => right.level - left.level || left.name.localeCompare(right.name, "zh-CN"));
report.generatedCount = records.length;
await atomicWrite(cachePath, cache);
await atomicWrite(reportPath, report);

if (report.errors.length > 0) {
  process.stderr.write(`生成失败：${report.errors.length} 个问题，详情见 ${reportPath}\n`);
  process.exitCode = 1;
} else {
  await atomicWrite(outputPath, {
    schemaVersion: 1,
    generatedAt: report.generatedAt,
    coordSystem: "GCJ-02",
    coverage: ["上海市", "江苏省", "浙江省"],
    items: records,
  });
  process.stdout.write(`已生成 ${records.length} 条景点数据：${outputPath}\n`);
}
