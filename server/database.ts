import pg from "pg";

let pool: pg.Pool | undefined;

export function database(): pg.Pool {
  const connectionString = process.env.DATABASE_URL;
  if (!connectionString) throw new Error("DATABASE_URL is not configured");
  if (!pool) {
    pool = new pg.Pool({
      connectionString,
      max: 5,
      idleTimeoutMillis: 10_000,
      connectionTimeoutMillis: 5_000,
      statement_timeout: 10_000,
      allowExitOnIdle: true,
      application_name: "roam-api",
    });
    // 不输出驱动错误正文，以免日志带出连接信息。
    pool.on("error", () => console.error("PostgreSQL idle connection failed"));
  }
  return pool;
}

export async function closeDatabase(): Promise<void> {
  const current = pool;
  pool = undefined;
  await current?.end();
}
