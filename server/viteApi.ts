import type { Connect, Plugin } from "vite";
import { handleAttractions } from "./attractionsHandler";

export function databaseApi(): Plugin {
  const middleware: Connect.NextHandleFunction = (request, response, next) => {
    const url = new URL(request.url ?? "/", "http://localhost");
    if (url.pathname !== "/api/attractions") return next();
    void handleAttractions(new Request(url, { method: request.method ?? "GET" }))
      .then(async (result) => {
        response.statusCode = result.status;
        result.headers.forEach((value, key) => response.setHeader(key, value));
        response.end(Buffer.from(await result.arrayBuffer()));
      }).catch(() => {
        response.statusCode = 503;
        response.setHeader("Content-Type", "application/json; charset=utf-8");
        response.end(JSON.stringify({ error: "景点服务暂时不可用" }));
      });
  };
  return {
    name: "roam-database-api",
    configureServer(server) { server.middlewares.use(middleware); },
    configurePreviewServer(server) { server.middlewares.use(middleware); },
  };
}
