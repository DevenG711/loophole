// Loophole's BehaviorGPT proxy (Cloudflare Worker).
//
// The shared API key lives here as a secret (UNBOXAI_API_KEY), never in the
// app. The app sends its ranking request here, and this Worker forwards it to
// BehaviorGPT with the real key.
//
// It forwards exactly one kind of call: POST /v1/vector/recommendations
// (the app's `client.complete(...)`), on Loophole's catalog only, stateless
// (register_event false, no user_id). Everything else gets an error, so the
// key can't upload catalogs, store events or read anything.

export const UPSTREAM = "https://behaviorgpt-northeurope.api.unboxai.com";
export const PATH = "/v1/vector/recommendations";
export const CATALOG_ID = "cat_35a0afc5ae400da9f74d27e2";
const MAX_BODY_BYTES = 64 * 1024; // a real request is about 3 KB
const MAX_HISTORY = 20;            // the app sends 8
const MAX_LIMIT = 60;              // the app asks for the whole 60-item catalog

function error(status, message) {
  return new Response(JSON.stringify({ error: message }), {
    status,
    headers: { "content-type": "application/json" },
  });
}

/** Why this request body is not allowed, or null if it is fine. */
export function rejectReason(body, catalogHeader) {
  if (!body || typeof body !== "object" || Array.isArray(body)) return "body must be a JSON object";
  if (body.store_id !== CATALOG_ID) return "only Loophole's catalog is allowed";
  if (catalogHeader && catalogHeader !== CATALOG_ID) return "only Loophole's catalog is allowed";
  if (body.register_event !== false) return "register_event must be false";
  if (body.user_id != null) return "user_id is not allowed";
  if ("query" in body) return "search is not allowed";
  if (!Array.isArray(body.history) || body.history.length === 0) return "history is required";
  if (body.history.length > MAX_HISTORY) return `history is limited to ${MAX_HISTORY} events`;
  for (const ev of body.history) {
    if (!ev || typeof ev !== "object" || ev.store_id !== CATALOG_ID) {
      return "every history event must use Loophole's catalog";
    }
  }
  if (!Number.isInteger(body.limit) || body.limit < 1 || body.limit > MAX_LIMIT) {
    return `limit must be 1-${MAX_LIMIT}`;
  }
  if (body.offset != null && body.offset !== 0) return "offset must be 0";
  return null;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/") {
      return new Response("Loophole proxy is running.\n");
    }
    if (url.pathname !== PATH) return error(404, "not found");
    if (request.method !== "POST") return error(405, "use POST");
    if (!env.UNBOXAI_API_KEY) return error(500, "proxy has no API key (wrangler secret put UNBOXAI_API_KEY)");

    // Per-IP rate limit (binding set in wrangler.toml). The app calls about
    // twice a minute during a session.
    if (env.LIMITER) {
      const ip = request.headers.get("cf-connecting-ip") || "unknown";
      const { success } = await env.LIMITER.limit({ key: ip });
      if (!success) return error(429, "too many requests, slow down");
    }

    const text = await request.text();
    if (text.length > MAX_BODY_BYTES) return error(413, "request too large");
    let body;
    try {
      body = JSON.parse(text);
    } catch {
      return error(400, "body must be JSON");
    }
    const reason = rejectReason(body, request.headers.get("x-catalog-id"));
    if (reason) return error(403, reason);

    const upstream = await fetch(UPSTREAM + PATH, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-api-key": env.UNBOXAI_API_KEY,
        "x-catalog-id": CATALOG_ID,
      },
      body: JSON.stringify(body),
    });
    // Pass the answer through, but never any upstream header that could
    // echo account details.
    return new Response(upstream.body, {
      status: upstream.status,
      headers: { "content-type": upstream.headers.get("content-type") || "application/json" },
    });
  },
};
