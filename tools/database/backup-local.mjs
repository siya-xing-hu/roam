import { spawn } from "node:child_process";
import { createWriteStream } from "node:fs";
import { mkdir, rename, rm } from "node:fs/promises";
import { pipeline } from "node:stream/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

const directory = fileURLToPath(new URL("../../infra/postgres/backups/", import.meta.url));
const stamp = new Date().toISOString().replaceAll(":", "-");
await mkdir(directory, { recursive: true, mode: 0o700 });
async function dump(name, args) {
  const temporary = path.join(directory, `${stamp}-${name}.partial`);
  const destination = path.join(directory, `${stamp}-${name}`);
  const process = spawn("docker", ["exec", "shared-postgres", ...args], { stdio: ["ignore", "pipe", "inherit"] });
  const completion = new Promise((resolve, reject) => {
    process.on("error", reject);
    process.on("exit", (code) => code === 0 ? resolve() : reject(new Error(`备份失败，退出码 ${code}`)));
  });
  try {
    await Promise.all([completion, pipeline(process.stdout, createWriteStream(temporary, { mode: 0o600, flags: "wx" }))]);
    await rename(temporary, destination);
    console.log(`备份完成：${destination}`);
  } catch (error) {
    process.kill();
    await rm(temporary, { force: true });
    throw error;
  }
}
await dump("roles.sql", ["pg_dumpall", "-U", "shared_admin", "--roles-only"]);
await dump("roam.dump", ["pg_dump", "-U", "shared_admin", "-d", "roam", "-Fc"]);
