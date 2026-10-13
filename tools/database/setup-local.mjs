import { randomBytes } from "node:crypto";
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync, chmodSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = fileURLToPath(new URL("../../", import.meta.url));
const directory = path.join(root, "infra/postgres");
const secretDirectory = path.join(directory, ".secrets");
const label = "io.roam.shared-postgres=18";
const docker = (args) => execFileSync("docker", args, { encoding: "utf8" }).trim();

// 先确认 Docker 可用；不覆盖任何已有凭据、容器或同名但来源不明的数据卷。
const volumes = docker(["volume", "ls", "--format", "{{.Name}}"] ).split("\n");
const existingVolume = volumes.includes("shared-postgres18-data");
if (existingVolume && docker(["volume", "inspect", "shared-postgres18-data", "--format", '{{index .Labels "io.roam.shared-postgres"}}']) !== "18") {
  throw new Error("同名数据卷已存在且不属于本配置；请先检查，未作修改。");
}
if (existingVolume && ["admin_password", "roam_owner_password", "roam_app_password"].some((name) => !existsSync(path.join(secretDirectory, name)))) {
  throw new Error("已有数据库卷但缺少凭据文件；请恢复原凭据，不能重新生成密码。");
}

mkdirSync(secretDirectory, { recursive: true, mode: 0o700 });
const credentials = {};
for (const name of ["admin_password", "roam_owner_password", "roam_app_password"]) {
  const target = path.join(secretDirectory, name);
  if (!existsSync(target)) writeFileSync(target, randomBytes(32).toString("hex") + "\n", { mode: 0o600, flag: "wx" });
  chmodSync(target, 0o600);
  credentials[name] = readFileSync(target, "utf8").trim();
  if (!credentials[name]) throw new Error(`凭据文件为空：${name}`);
}
const environmentPath = path.join(root, ".env.local");
const previousEnvironment = existsSync(environmentPath) ? readFileSync(environmentPath, "utf8") : "";
const variables = {
  DATABASE_URL: `postgresql://roam_app:${encodeURIComponent(credentials.roam_app_password)}@127.0.0.1:5432/roam`,
  DATABASE_MIGRATION_URL: `postgresql://roam_owner:${encodeURIComponent(credentials.roam_owner_password)}@127.0.0.1:5432/roam`,
};
const missingVariables = Object.entries(variables).filter(([key]) => !new RegExp(`^${key}=`, "m").test(previousEnvironment));
if (missingVariables.length) {
  writeFileSync(environmentPath, previousEnvironment + (previousEnvironment.endsWith("\n") || !previousEnvironment ? "" : "\n")
    + missingVariables.map(([key, value]) => `${key}=${value}`).join("\n") + "\n", { mode: 0o600 });
}
chmodSync(environmentPath, 0o600);

if (!existingVolume) docker(["volume", "create", "--label", label, "shared-postgres18-data"]);
const networks = docker(["network", "ls", "--format", "{{.Name}}"] ).split("\n");
if (!networks.includes("shared-postgres-network")) docker(["network", "create", "--label", label, "shared-postgres-network"]);
execFileSync("docker", ["compose", "-f", path.join(directory, "compose.yaml"), "up", "-d", "--wait", "--wait-timeout", "90"], { stdio: "inherit" });
console.log("共享 PostgreSQL 已启动：127.0.0.1:5432，roam 数据库；凭据保存在 .env.local 和 infra/postgres/.secrets/。");
