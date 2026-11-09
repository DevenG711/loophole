// Cloudflare Worker version of Loophole's BehaviorGPT proxy. The main one is
// the Vercel function in site/api/rank.js; both use the rules in
// site/api/_proxy.js. See proxy/README.md.
import { PATH, error, forward } from "../../site/api/_proxy.js";

export { CATALOG_ID, PATH, UPSTREAM, rejectReason } from "../../site/api/_proxy.js";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/") {
      return new Response("Loophole proxy is running.\n");
    }
    if (url.pathname !== PATH) return error(404, "not found");
    return forward(request, {
      key: env.UNBOXAI_API_KEY,
      // Per-IP limit from the LIMITER binding in wrangler.toml.
      allow: env.LIMITER ? async (ip) => (await env.LIMITER.limit({ key: ip })).success : null,
      ip: request.headers.get("cf-connecting-ip"),
    });
  },
};
