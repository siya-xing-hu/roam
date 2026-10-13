import { database } from "./database";
import { InvalidQuery, parseCatalogQuery, readCatalog } from "./catalog";

export async function handleAttractions(request: Request): Promise<Response> {
  if (request.method !== "GET" && request.method !== "HEAD") {
    return Response.json({ error: "只支持读取景点" }, { status: 405, headers: { Allow: "GET, HEAD" } });
  }
  try {
    const query = parseCatalogQuery(new URL(request.url).searchParams);
    const dataset = await readCatalog(database(), query);
    return new Response(request.method === "HEAD" ? null : JSON.stringify(dataset), {
      headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-cache" },
    });
  } catch (error) {
    const invalid = error instanceof InvalidQuery;
    if (!invalid) console.error("Attraction database query failed");
    return Response.json({ error: invalid ? error.message : "景点数据暂时不可用，请稍后重试" }, {
      status: invalid ? 400 : 503, headers: { "Cache-Control": "no-store" },
    });
  }
}
