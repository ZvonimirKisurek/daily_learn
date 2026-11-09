import { ApiError, getFeed } from "@/lib/api";
import type { Category, FeedQuery } from "@/lib/types";

const CATEGORIES = new Set(["research", "industry_news", "engineering_blog", "community", "newsletter", "researcher"]);

/**
 * Same-origin proxy ("backend for frontend") used by the feed's "Load more" button. The browser calls
 * /api/feed on THIS origin (no CORS), and this handler adds the secret bearer token server-side before
 * calling FastAPI. Query params are whitelisted/validated here, not blindly forwarded.
 */
export async function GET(req: Request) {
  const sp = new URL(req.url).searchParams;
  const category = sp.get("category") ?? undefined;
  const sort = sp.get("sort") ?? undefined;
  const skill = sp.get("skill") ?? undefined;
  const cursor = sp.get("cursor") ?? undefined;
  const minStrength = sp.get("min_strength") ?? undefined;
  if (category && !CATEGORIES.has(category)) return Response.json({ error: "bad category" }, { status: 400 });
  if (sort && sort !== "recent" && sort !== "relevance" && sort !== "for_you") return Response.json({ error: "bad sort" }, { status: 400 });
  if (skill && !/^[a-z0-9-]{1,40}$/.test(skill)) return Response.json({ error: "bad skill" }, { status: 400 });
  if (minStrength && minStrength !== "moderate" && minStrength !== "strong") return Response.json({ error: "bad min_strength" }, { status: 400 });
  const q: FeedQuery = { category: category as Category | undefined, sort: sort as FeedQuery["sort"], min_strength: minStrength as FeedQuery["min_strength"], skill, cursor };
  try {
    return Response.json(await getFeed(q), { headers: { "Cache-Control": "no-store" } });
  } catch (e) {
    const status = e instanceof ApiError && e.kind === "client" ? 400 : 502;
    return Response.json({ error: e instanceof ApiError ? e.message : "upstream error" }, { status });
  }
}
