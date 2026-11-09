# Loophole proxy

A Cloudflare Worker that holds the shared BehaviorGPT API key, so people who
download Loophole need no key and never see one.

```
Loophole on each Mac  →  this Worker (has the key)  →  BehaviorGPT
```

It forwards only the one call the app makes (`POST /v1/vector/recommendations`,
the app's `client.complete(...)`), only on Loophole's catalog, only stateless
(`register_event: false`, no `user_id`), with at most 20 history events. Each
IP gets 10 requests a minute; the app makes about 2. Anything else gets an
error and never reaches BehaviorGPT, so the key can't upload catalogs or store
events.

## Deploy (once, about 5 minutes)

You need a free Cloudflare account and Node.js.

```bash
cd proxy
npx wrangler login                        # opens the browser to sign in
npx wrangler secret put UNBOXAI_API_KEY   # paste the key when asked
npx wrangler deploy
```

`deploy` prints the Worker's address, like
`https://loophole-proxy.<your-subdomain>.workers.dev`. Then:

1. Open that address in a browser. It should say "Loophole proxy is running."
2. Put the address in `DEFAULT_PROXY_URL` in
   [../loophole/config.py](../loophole/config.py), and commit and push. Every
   new download then uses it.

To change the key later, run `npx wrangler secret put UNBOXAI_API_KEY` again.
No redeploy and no app update needed.

## Test

```bash
node --test proxy/test.mjs
```

No network: BehaviorGPT is faked.

## Watch it

Cloudflare dashboard > Workers & Pages > loophole-proxy shows requests and
errors. The free plan allows 100,000 requests a day, which is about 400 hours
of focus sessions.
