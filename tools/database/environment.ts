import { existsSync, readFileSync } from "node:fs";
import { parseEnv } from "node:util";
import { fileURLToPath } from "node:url";
import pg from "pg";

export const root = fileURLToPath(new URL("../../", import.meta.url));

export function loadDatabaseEnvironment(): void {
  // 与 Vite 一致：shell 环境优先，其次 .env.local，再次 .env。
  for (const name of [".env.local", ".env"]) {
    const file = new URL(`../../${name}`, import.meta.url);
    if (existsSync(file)) {
      for (const [key, value] of Object.entries(parseEnv(readFileSync(file, "utf8")))) {
        if (process.env[key] === undefined) process.env[key] = value;
      }
    }
  }
}

export function migrationPool(): pg.Pool {
  loadDatabaseEnvironment();
  const connectionString = process.env.DATABASE_MIGRATION_URL;
  if (!connectionString) throw new Error("请配置 DATABASE_MIGRATION_URL，或先运行 npm run db:up");
  return new pg.Pool({ connectionString, max: 1, connectionTimeoutMillis: 5_000, application_name: "roam-import" });
}

export function reportDatabaseError(error: unknown): void {
  // 不回显连接串、SQL 参数或可能包含凭据的驱动错误正文。
  const code = typeof error === "object" && error && "code" in error ? String(error.code) : "unknown";
  console.error(`数据库操作失败（${code}）；请检查连接、账号权限和迁移状态。`);
  process.exitCode = 1;
}
