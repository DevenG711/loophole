// Vercel function: Loophole's BehaviorGPT proxy (rules in ./_proxy.js).
// vercel.json sends /v1/vector/recommendations here, so the app's
// LOOPHOLE_PROXY_URL is just the site's address.
//
// Environment variables (Vercel > Project > Settings > Environment Variables):
//   UNBOXAI_API_KEY           required, the shared BehaviorGPT key
//   KV_REST_API_URL/_TOKEN    optional, an Upstash Redis store for the per-IP
//   (or UPSTASH_REDIS_REST_*)  rate limit. Without one there is no limit.
import { RATE_LIMIT_PER_MIN, forward } from "./_proxy.js";

function clientIp(request) {
  const fwd = request.headers.get("x-forwarded-for");
  return (fwd && fwd.split(",")[0].trim()) || request.headers.get("x-real-ip") || "unknown";
}

/** Fixed one-minute window per IP in Upstash Redis. Fails open if Redis is down. */
export async function upstashAllow(ip, env = process.env) {
  const url = env.KV_REST_API_URL || env.UPSTASH_REDIS_REST_URL;
  const token = env.KV_REST_API_TOKEN || env.UPSTASH_REDIS_REST_TOKEN;
  if (!url || !token) return true;
  const key = `loophole:rl:${ip}:${Math.floor(Date.now() / 60000)}`;
  try {
    const resp = await fetch(`${url}/pipeline`, {
      method: "POST",
      headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
      body: JSON.stringify([["INCR", key], ["EXPIRE", key, "120"]]),
    });
    if (!resp.ok) return true;
    const [incr] = await resp.json();
    return Number(incr?.result) <= RATE_LIMIT_PER_MIN;
  } catch {
    return true;
  }
}

export function GET() {
  return new Response("Loophole proxy is running.\n");
}

export function POST(request) {
  return forward(request, {
    key: process.env.UNBOXAI_API_KEY,
    allow: (ip) => upstashAllow(ip),
    ip: clientIp(request),
  });
}
