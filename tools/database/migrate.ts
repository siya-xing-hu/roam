import { createHash } from "node:crypto";
import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import { migrationPool, reportDatabaseError, root } from "./environment";

const pool = migrationPool();
try {
  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    await client.query("SELECT pg_advisory_xact_lock(726626, 1)");
    await client.query("CREATE SCHEMA IF NOT EXISTS roam");
    await client.query(`CREATE TABLE IF NOT EXISTS roam.schema_migrations (
      version text PRIMARY KEY, sha256 text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now()
    )`);
    const directory = path.join(root, "database/migrations");
    for (const file of (await readdir(directory)).filter((name) => name.endsWith(".sql")).sort()) {
      const sql = await readFile(path.join(directory, file), "utf8");
      const hash = createHash("sha256").update(sql).digest("hex");
      const previous = await client.query<{ sha256: string }>("SELECT sha256 FROM roam.schema_migrations WHERE version = $1", [file]);
      if (previous.rows[0]) {
        if (previous.rows[0].sha256 !== hash) throw new Error(`已应用的迁移被修改：${file}`);
        continue;
      }
      await client.query(sql);
      await client.query("INSERT INTO roam.schema_migrations(version, sha256) VALUES ($1, $2)", [file, hash]);
      console.log(`应用迁移：${file}`);
    }
    await client.query("COMMIT");
    console.log("数据库结构已就绪。");
  } catch (error) {
    await client.query("ROLLBACK");
    throw error;
  } finally {
    client.release();
  }
} catch (error) {
  reportDatabaseError(error);
} finally {
  await pool.end();
}
