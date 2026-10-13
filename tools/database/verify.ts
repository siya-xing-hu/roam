import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import pg from "pg";
import type { AttractionDataset } from "../../src/types";
import { parseCatalogQuery, readCatalog } from "../../server/catalog";
import { closeDatabase, database } from "../../server/database";
import { handleAttractions } from "../../server/attractionsHandler";
import { loadDatabaseEnvironment, reportDatabaseError } from "./environment";

loadDatabaseEnvironment();
try {
  const expected = JSON.parse(await readFile(new URL("../../public/data/attractions.json", import.meta.url), "utf8")) as AttractionDataset;
  assert.deepEqual(await readCatalog(database()), expected, "数据库应完整保留现有景点、排序、坐标来源和元信息");
  const region = await readCatalog(database(), { region: "310115" });
  assert.deepEqual(region.items, expected.items.filter((item) => item.adcode === "310115"));
  const city = await readCatalog(database(), { region: "310000" });
  assert.deepEqual(city.items, expected.items.filter((item) => item.adcode.startsWith("31")));
  const top = await readCatalog(database(), { region: "310000", limit: 20 });
  assert.equal(top.items.length, Math.min(20, city.items.length));
  assert.equal(new Set(top.items.map((item) => item.id)).size, top.items.length);
  assert.ok(top.items.every((item, index) => !index || top.items[index - 1].level >= item.level));
  const bounds: [number, number, number, number] = [121.3, 30.9, 121.9, 31.4];
  const inView = await readCatalog(database(), { bounds });
  assert.deepEqual(inView.items, expected.items.filter((item) => item.lng >= bounds[0] && item.lng <= bounds[2]
    && item.lat >= bounds[1] && item.lat <= bounds[3]));
  const directCity = await readCatalog(database(), { region: "429004" });
  assert.ok(directCity.items.every((item) => item.adcode === "429004"), "省直辖城市不能误包含其他县市");
  for (const query of ["limit=0", "limit=1001", "region=310115%27", "bbox=0,0,0,1", "bbox=,0,1,1", "limit=1&limit=2"]) {
    assert.throws(() => parseCatalogQuery(new URLSearchParams(query)));
    assert.equal((await handleAttractions(new Request(`http://localhost/api/attractions?${query}`))).status, 400);
  }
  const response = await handleAttractions(new Request("http://localhost/api/attractions?region=310115"));
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), region);
  assert.equal((await handleAttractions(new Request("http://localhost/api/attractions", { method: "POST" }))).status, 405);
  const head = await handleAttractions(new Request("http://localhost/api/attractions", { method: "HEAD" }));
  assert.equal(head.status, 200);
  assert.equal(await head.text(), "");
  await assert.rejects(database().query("UPDATE roam.attractions SET name = name WHERE false"),
    (error: unknown) => (error as { code?: string }).code === "42501", "应用账号不应具有写权限");
  const otherDatabase = new URL(process.env.DATABASE_URL!);
  otherDatabase.pathname = "/postgres";
  const forbidden = new pg.Pool({ connectionString: otherDatabase.toString(), max: 1, connectionTimeoutMillis: 5_000 });
  try {
    await assert.rejects(forbidden.query("SELECT 1"), (error: unknown) => (error as { code?: string }).code === "42501",
      "应用账号不应连接管理数据库");
  } finally {
    await forbidden.end();
  }
  console.log(`验证通过：${expected.items.length} 个景点完整往返、区域/等级/视口查询、HTTP 校验、只读账号和跨库隔离。`);
} catch (error) {
  if (error instanceof assert.AssertionError) {
    console.error(`验证失败：${error.message.split("\n")[0]}`);
    process.exitCode = 1;
  } else reportDatabaseError(error);
} finally {
  await closeDatabase();
}
