import { defineConfig, loadEnv } from "vite";
import { databaseApi } from "./server/viteApi";

export default defineConfig(({ mode }) => {
  const environment = loadEnv(mode, process.cwd(), "");
  if (!process.env.DATABASE_URL && environment.DATABASE_URL) process.env.DATABASE_URL = environment.DATABASE_URL;
  return {
    base: "./",
    plugins: [databaseApi()],
    server: {
      host: "127.0.0.1",
      port: 5175,
      strictPort: true,
    },
    preview: {
      host: "127.0.0.1",
      port: 5175,
      strictPort: true,
    },
  };
});
