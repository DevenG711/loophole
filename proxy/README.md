# Loophole proxy

The shared BehaviorGPT API key lives on a server, so people who download
Loophole need no key and never see one.

```
Loophole on each Mac  →  proxy (has the key)  →  BehaviorGPT
```

The proxy forwards only the one call the app makes
(`POST /v1/vector/recommendations`, the app's `client.complete(...)`), only on
Loophole's catalog, only stateless (`register_event: false`, no `user_id`),
with at most 20 history events. Anything else gets an error and never reaches
BehaviorGPT, so the key can't upload catalogs or store events.

There are two versions with the same rules ([site/api/_proxy.js](../site/api/_proxy.js)):

- **Vercel** (main): [site/api/rank.js](../site/api/rank.js), deployed with
  the landing page. One project, one address.
- **Cloudflare Worker** (backup): [src/index.js](src/index.js).

## Deploy on Vercel (with the landing page)

You need a free Vercel account. The GitHub repo can stay private for this.

1. Go to [vercel.com/new](https://vercel.com/new) and import the
   `DevenG711/loophole` repo.
2. On the setup screen:
   - **Root Directory:** click Edit and pick `site`.
   - **Framework Preset:** Other. Leave the build and output settings empty.
   - **Environment Variables:** add `UNBOXAI_API_KEY` with your key as the value.
3. Click **Deploy**. Vercel gives you an address like
   `https://loophole-xyz.vercel.app`.
4. Check it: open `https://<your-address>/api/rank` in a browser. It should
   say "Loophole proxy is running." The address itself shows the landing page.
5. Put the address (no path, no `/` at the end) in `DEFAULT_PROXY_URL` in
   [../loophole/config.py](../loophole/config.py), then commit and push.
   Every new download then uses it.

Later pushes to `main` redeploy the site and the proxy on their own. To change
the key: Settings > Environment Variables, edit it, then Deployments > the
latest one > **Redeploy** (Vercel only reads new variables on a new deploy).

### Rate limit (optional, recommended)

Without it, someone could call the proxy in a loop and use up your
BehaviorGPT quota (they still can't see the key or do anything else).

1. In the Vercel project: **Storage** > **Create Database** > **Upstash for
   Redis** (free plan) > connect it to this project.
2. That adds `KV_REST_API_URL` and `KV_REST_API_TOKEN` to the project. Redeploy.

Each IP then gets 10 requests a minute; the app makes about 2. If Redis is
down, requests go through rather than breaking the app.

### Command line instead

```bash
cd site
npx vercel login
npx vercel link                          # make or pick the project
npx vercel env add UNBOXAI_API_KEY production   # paste the key when asked
npx vercel deploy --prod
```

## Deploy on Cloudflare instead

```bash
cd proxy
npx wrangler login
npx wrangler secret put UNBOXAI_API_KEY   # paste the key when asked
npx wrangler deploy
```

The rate limit is built in (`wrangler.toml`). Use the printed
`https://loophole-proxy.<you>.workers.dev` address as `DEFAULT_PROXY_URL`.

## Test

```bash
node --test proxy/test.mjs
```

This tests both versions. There's no network: BehaviorGPT and Redis are faked.
