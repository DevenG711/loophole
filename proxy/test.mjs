// Run: node --test proxy/test.mjs   (no network: BehaviorGPT is faked)
import { test } from "node:test";
import assert from "node:assert/strict";
import worker, { CATALOG_ID, PATH, UPSTREAM, rejectReason } from "./src/index.js";

const ev = (product) => ({ store_id: CATALOG_ID, event: { type: "viewItem", product } });
const good = () => ({
  history: [ev("f_vscode"), ev("d_instagram")],
  store_id: CATALOG_ID, limit: 60, offset: 0, filters: {}, register_event: false,
});

let calls = [];
globalThis.fetch = async (url, init) => {
  calls.push({ url, init });
  return new Response(JSON.stringify({ items: [{ id: "d_tiktok" }] }), {
    status: 200, headers: { "content-type": "application/json", "x-secret": "no" },
  });
};
const env = { UNBOXAI_API_KEY: "real-key" };
const post = (body, headers = {}) => new Request("https://proxy.test" + PATH, {
  method: "POST", body: typeof body === "string" ? body : JSON.stringify(body),
  headers: { "content-type": "application/json", "x-api-key": "loophole-proxy", ...headers },
});

test("forwards a good request with the real key", async () => {
  calls = [];
  const resp = await worker.fetch(post(good()), env);
  assert.equal(resp.status, 200);
  assert.deepEqual(await resp.json(), { items: [{ id: "d_tiktok" }] });
  assert.equal(resp.headers.get("x-secret"), null);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, UPSTREAM + PATH);
  assert.equal(calls[0].init.headers["x-api-key"], "real-key");
});

test("rejects anything but the ranking call, without calling upstream", async () => {
  calls = [];
  const bad = [
    { ...good(), store_id: "cat_other" },
    { ...good(), register_event: true },
    { ...good(), user_id: "someone" },
    { ...good(), query: "tiktok" },
    { ...good(), limit: 500 },
    { ...good(), history: [] },
    { ...good(), history: [{ ...ev("f_vscode"), store_id: "cat_other" }] },
  ];
  for (const b of bad) assert.equal((await worker.fetch(post(b), env)).status, 403, JSON.stringify(b));
  assert.equal((await worker.fetch(post(good(), { "x-catalog-id": "cat_other" }), env)).status, 403);
  assert.equal((await worker.fetch(post("not json"), env)).status, 400);
  assert.equal((await worker.fetch(new Request("https://proxy.test/v1/catalogs", { method: "POST", body: "{}" }), env)).status, 404);
  assert.equal((await worker.fetch(new Request("https://proxy.test" + PATH), env)).status, 405);
  assert.equal(calls.length, 0);
});

test("rate limit returns 429", async () => {
  calls = [];
  const limited = { ...env, LIMITER: { limit: async () => ({ success: false }) } };
  assert.equal((await worker.fetch(post(good()), limited)).status, 429);
  assert.equal(calls.length, 0);
});

test("missing secret is a clear error", async () => {
  assert.equal((await worker.fetch(post(good()), {})).status, 500);
});

test("rejectReason accepts the app's real shape", () => {
  assert.equal(rejectReason(good(), CATALOG_ID), null);
});

// ---- Vercel function (site/api/rank.js) ----
const vercel = await import("../site/api/rank.js");

test("vercel: GET is a health check", async () => {
  assert.equal(await vercel.GET().text(), "Loophole proxy is running.\n");
});

test("vercel: POST forwards with the key from the environment", async () => {
  calls = [];
  process.env.UNBOXAI_API_KEY = "vercel-key";
  delete process.env.KV_REST_API_URL;
  const resp = await vercel.POST(post(good()));
  assert.equal(resp.status, 200);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].init.headers["x-api-key"], "vercel-key");
  assert.equal((await vercel.POST(post({ ...good(), store_id: "cat_other" }))).status, 403);
  delete process.env.UNBOXAI_API_KEY;
});

test("vercel: Upstash rate limit allows 10 a minute, fails open", async () => {
  const real = globalThis.fetch;
  let count = 0;
  globalThis.fetch = async (url, init) => {
    assert.ok(url.endsWith("/pipeline"));
    assert.equal(init.headers.authorization, "Bearer t");
    count += 1;
    return new Response(JSON.stringify([{ result: count }, { result: 1 }]));
  };
  const env = { KV_REST_API_URL: "https://redis.test", KV_REST_API_TOKEN: "t" };
  for (let i = 0; i < 10; i++) assert.equal(await vercel.upstashAllow("1.2.3.4", env), true);
  assert.equal(await vercel.upstashAllow("1.2.3.4", env), false);
  assert.equal(await vercel.upstashAllow("1.2.3.4", {}), true);   // no Redis: no limit
  globalThis.fetch = async () => { throw new Error("down"); };
  assert.equal(await vercel.upstashAllow("1.2.3.4", env), true);  // Redis down: let it through
  globalThis.fetch = real;
});
