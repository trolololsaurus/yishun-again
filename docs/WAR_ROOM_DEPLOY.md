# War Room — Cloudflare Access Setup & Deployment

The War Room (`warroom.yishunagain.com`) is a private operator CMS. It must
never be reachable without Cloudflare Access authentication. This document
covers the full setup from a fresh Cloudflare account to a locked-down
production deployment.

---

## 1. Prerequisites

- `warroom.yishunagain.com` is a DNS record in your Cloudflare zone pointing
  at the Vercel deployment (proxied, orange-cloud ☁).
- Cloudflare Zero Trust is enabled on the account (free tier is sufficient).

### DNS records (Cloudflare zone `yishunagain.com`)

There is no DNS-as-code in this repo — every record is managed by hand in the
Cloudflare dashboard. This table is the source of truth for what must exist.
The `warroom` row is the one this document depends on.

| Name | Type | Target | Proxy | Serves |
|---|---|---|---|---|
| `warroom` | CNAME | Vercel target for the `war-room` project (`cname.vercel-dns.com` unless Vercel assigns a per-project host — copy the exact value from the Vercel project's **Domains** tab) | **Proxied ☁ (required)** | War Room CMS, behind Cloudflare Access |
| `www` | CNAME | Vercel target for the `web` project | Proxied ☁ | Public site — canonical host ([site.ts](../apps/web/lib/site.ts)) |
| `yishunagain.com` (apex) | — | 308-redirect to `www` (Cloudflare Redirect Rule, or Vercel domain redirect) | Proxied ☁ | Apex → www. A POST does **not** survive this redirect (§5, `NEXT_PUBLIC_SITE_URL`) |
| `assets` | CNAME | R2 custom-domain binding for the `yishun-assets` bucket (created from the R2 dashboard, which writes this record) | Proxied ☁ | Pixel-art / OG images ([r2.config.js](../infra/cloudflare/r2.config.js)) |

⚠️ **`warroom` must be Proxied (orange cloud ☁), not DNS-only.** Cloudflare
Access only intercepts proxied records. If this record is ever switched to
DNS-only (grey cloud), the Access login screen stops appearing, no CF-signed JWT
is ever issued, and `proxy.ts` (§6) 403s every request — the gate fails closed,
but the CMS is simply unreachable until the record is proxied again. A `503`
means the app is up but `CF_ACCESS_*` is unset; a login screen that never
appears means the record went grey.

The agents backend has **no custom domain** — the War Room reaches it at its
raw Cloud Run `*.a.run.app` host via `AGENTS_API_URL` (§5a), so it needs no DNS
record here.

---

## 2. Create the Cloudflare Access Application

1. Go to **Cloudflare dashboard → Zero Trust → Access → Applications**.
2. Click **Add an application → Self-hosted**.
3. Fill in the form:

   | Field | Value |
   |---|---|
   | Application name | `War Room` |
   | Session duration | `24 hours` |
   | Application domain | `warroom.yishunagain.com` |
   | Path | *(leave blank — protect the whole domain)* |

4. Click **Next**.

---

## 3. Create the Access Policy

1. Policy name: `Operator only`
2. Action: **Allow**
3. Under **Configure rules**, add one Include rule:
   - Selector: **Emails**
   - Value: the operator's address — the same value as `OPERATOR_EMAIL` in §5
4. Click **Next**, then **Add application**.

This means only that email can pass the Cloudflare login screen. Anyone else
(including you with a different email) is blocked at the edge before the request
reaches Vercel.

Once the application exists, collect the two values `proxy.ts` verifies against
(§5, §6) — it refuses to serve at all until both are set:

- **Application Audience (AUD) Tag** — on the application's overview/settings
  page. This is `CF_ACCESS_AUD`.
- **Team domain** — the `<team>.cloudflareaccess.com` host in the Access login
  URL. This is `CF_ACCESS_TEAM_DOMAIN`. `proxy.ts` strips a leading `https://`
  and any trailing slashes, so either form works.

---

## 4. Service Tokens (optional — nothing uses one today)

`proxy.ts` accepts two kinds of Access JWT: an **identity login**, which carries
an `email` claim, and a **service token**, which carries `common_name` instead.
Service-token JWTs are allowed through without an email check — the caller
already proved possession of the client secret to Cloudflare.

⚠️ **No caller in this repo uses one.** Traffic between the two services runs
the other way: the War Room calls the agents backend (§5a), never the reverse.
Nothing in `packages/agents/` makes an HTTP request to the War Room —
`ops/notify.py::war_room_url()` only builds links for operator emails. Neither
`CF_ACCESS_CLIENT_ID` nor `CF_ACCESS_CLIENT_SECRET` is read anywhere in the
codebase, and neither belongs in Cloud Run env vars today.

Kept here because the middleware path exists and is the right answer if a
server-to-server caller is ever added:

1. Go to **Zero Trust → Access → Service Auth → Service Tokens**.
2. Click **Create Service Token**.
3. Name it, e.g. `yishun-agents-backend`.
4. Copy the **Client ID** and **Client Secret** — the secret is only shown once.
5. Back in the War Room application, go to **Policies** and add a second policy:
   - Policy name: `Service token`
   - Action: **Service Auth**
   - Include rule: **Service Token** → the token from step 3

The caller then sends `CF-Access-Client-Id` and `CF-Access-Client-Secret`;
Cloudflare exchanges them for a JWT carrying `common_name`, which is what
`proxy.ts` verifies.

---

## 5. Environment Variables

Set these in the Vercel project for the `war-room` app (Production environment):

| Variable | Value | Notes |
|---|---|---|
| `SUPABASE_URL` | `https://xxxx.supabase.co` | From Supabase project settings |
| `SUPABASE_SECRET_KEY` | `eyJ...` | Service role key — bypasses RLS |
| `CF_ACCESS_TEAM_DOMAIN` | `<team>.cloudflareaccess.com` | **Required.** JWKS issuer — unset ⇒ every request 503s |
| `CF_ACCESS_AUD` | *(AUD tag from §3)* | **Required.** Same — the gate fails closed |
| `OPERATOR_EMAIL` | *(the operator's address)* | Optional extra allowlist. Compared case-insensitively |
| `NEXT_PUBLIC_SITE_URL` | `https://www.yishunagain.com` | **www, not the apex** — see below |
| `WAR_ROOM_URL` | `https://warroom.yishunagain.com` | |
| `AGENTS_API_URL` | `https://yishun-agents-xxxxx-as.a.run.app` | Image generation (Track B) — see §5a |
| `OPS_TOKEN` | *(same value as Cloud Run)* | Byte-identical or every render 401s |
| `REVALIDATE_SECRET` | *(same value as the `web` project)* | Byte-identical or rectification silently no-ops |
| `ART_TIMEOUT_MS` | `50000` | Optional; code default. Approve path. **Must stay under 60000** |
| `RECTIFY_TIMEOUT_MS` | `40000` | Optional; code default. One attempt, no ladder. **Must stay under 60000** |

⚠️ **The two `CF_ACCESS_*` vars are not optional.** `proxy.ts` fails closed: with
either unset in production it answers every request `503 War Room auth not
configured…`, deliberately, so a misconfigured deploy cannot become an open CMS.
`CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET` are a different thing entirely
(§4) and are read by nothing — do not set them here.

`OPERATOR_EMAIL` is the second line of defence in `proxy.ts`: a JWT that
verifies but carries a different `email` claim is rejected with 403. The primary
defence is the Cloudflare policy above. Two limits worth knowing: it is skipped
when unset, and it does not apply to service-token JWTs, which carry
`common_name` and no email. Case does not matter — both sides are lowercased
before comparison.

⚠️ **The two art timeouts must stay strictly below 60 s.** Both routes declare
`maxDuration = 60`, and Vercel kills the function at that ceiling regardless of
any `AbortController`. In the approve route the art call precedes the incident
INSERT, so a platform kill loses the whole approval, not just the picture —
whereas the in-handler abort degrades to `status: 'transient'` and still
publishes. This table previously listed `120000` and `45000`, both of which lose
that race. Regression guard: `packages/agents/test_rectify_guards.py` (#1).

⚠️ **`NEXT_PUBLIC_SITE_URL` must carry the `www` host.** This table previously
said the apex. The War Room's rectification route POSTs to
`{NEXT_PUBLIC_SITE_URL}/api/revalidate`; against the apex that hits the
apex→www redirect, which a POST does not survive. `lib/revalidate.ts` sets
`redirect: 'error'` so the misconfiguration fails loudly rather than degrading
to a silent 405.

---

## 5a. Image generation — the War Room calls the agents backend

`pixel_art_url` has two writers: `ops/auto_publish.py` (Python, imports the
generator directly) and this app's approve route (TypeScript, cannot). The War
Room therefore calls `POST /art/generate` and `POST /art/rectify` on Cloud Run
with `X-Ops-Token`. Reimplementing the guardrail-#5 suppression gate and the
softening ladder in TypeScript would put the one check that must not fail into
two languages in two repos.

### ✅ RESOLVED 2026-08-04 — was: blocked as deployed, `X-Ops-Token` is not enough

`yishun-agents` was deployed `--no-allow-unauthenticated`, with its only IAM
binding `roles/run.invoker` for `yishun-scheduler@…`. Vercel has no identity
there, so Google's IAM layer answered **403 before the app ever read the
header** — verified 2026-08-01 against the live service.

**This section diagnosed the bug correctly on 2026-08-01 and nothing acted on it
for three days**, during which every `/art/generate` call 403'd, `image_prompt`
stayed NULL on all 172 incidents, and the archive held exactly one image. Writing
the diagnosis down is not the same as fixing it; if you find a block like this
again, change the config in the same commit.

Fixed by granting `allUsers` `roles/run.invoker` **and** flipping the flag in
`infra/cloudbuild.yaml` — the binding alone does not survive, because
`--no-allow-unauthenticated` rewrites the IAM policy on every deploy and drops
`allUsers`. That is precisely how the fix was reverted 8 minutes after it was
first applied. `OPS_TOKEN` is the gate now.

Until this is resolved every render returns `status: 'transient'`. On the
approve path that means the incident publishes with `pixel_art_url` null and the
operator sees no error at all — the route logs `art/generate — HTTP 403` and
inserts anyway, because nothing here is worth losing a publish over. On
`/rectify` the status at least reaches the card. Two options:

1. **Make the service publicly invokable** and rely on `OPS_TOKEN` as the only
   gate — one command, but the token becomes the entire perimeter:
   ```
   gcloud run services add-iam-policy-binding yishun-agents \
     --region asia-southeast1 \
     --member="allUsers" --role="roles/run.invoker"
   ```
2. **Mint a Google identity token per request** from a service account Vercel
   holds, and send it as `Authorization: Bearer`. Keeps IAM closed; more work,
   and `lib/artGenerate.ts` would need to acquire and cache the token.

The agents backend also needs `CF_R2_*`, `IMAGE_MODEL` and
`ART_GENERATION_ENABLED=true` before it will render anything — see
`.env.example`. `ART_GENERATION_ENABLED` defaults to `false` precisely so an
unconfigured deploy behaves exactly as it does today.

---

## 6. How the Middleware Works

The file is `apps/war-room/proxy.ts`, **not `middleware.ts`** — Next.js 16
renamed the convention, and refuses to build if both exist. `proxy` always runs
on the Node.js runtime; the edge runtime is unsupported there and cannot be
configured. That suits this gate: JWT signature verification wants Node's full
crypto, not just WebCrypto.

It runs on every request except:
- `_next/static/*` — static asset chunks
- `_next/image/*` — image optimisation
- `favicon.ico`

`/api/health` is **no longer exempt**. It returns real operational data (scraper
fleet health, queue counts, pending pattern alerts), and the spec says the War
Room has no bypass route. Browser calls carry the `CF_Authorization` cookie, so
they pass the JWT check anyway.

In **production** (`NODE_ENV=production`), in order:

1. **CSRF check.** For anything other than `GET`/`HEAD`, an `Origin` header that
   doesn't match the request origin → `403 Cross-origin request rejected`.
   Server-to-server calls send no `Origin` and pass.
2. **Rate limit**, `/api/*` only, per-instance fixed window keyed on
   `x-real-ip` / `cf-connecting-ip`:
   - 60 req/min for ordinary API routes (the Phase 1 spec limit);
   - **10 req/min** for `/api/queue/<id>/approve` and
     `/api/incidents/<id>/rectify`, in their own namespace. These spend ~$0.0336
     of Gemini per call, so a request ceiling is not a cost ceiling — 60/min
     against them permits about $2/min per IP, which a stuck retry loop in one
     browser tab reaches unnoticed.
   - Over the limit → `429 Too many requests`.
3. **Config check.** `CF_ACCESS_TEAM_DOMAIN` or `CF_ACCESS_AUD` unset →
   `503 War Room auth not configured…`. Fails closed on purpose.
4. **Token.** Reads the `Cf-Access-Jwt-Assertion` header, falling back to the
   `CF_Authorization` cookie. Missing → `403 Access denied`.
5. **Verification.** `jwtVerify` against the team's JWKS
   (`https://<team domain>/cdn-cgi/access/certs`, cached by `jose` on a warm
   instance), checking `issuer` and `audience`. Any failure → `403`.
6. **Identity.** An `email` claim (identity login) or a `common_name` claim
   (service token) is required; neither → `403`. If `OPERATOR_EMAIL` is set, an
   identity login whose email doesn't match → `403`. Service tokens skip that
   check.
7. Otherwise → request passes through to the Next.js app.

> **Why a JWT and not the header.** The old check was the presence of the
> `cf-access-authenticated-user-email` header. Plain request headers prove
> nothing: anyone who could reach the origin directly — a leaked `*.vercel.app`
> URL, say — could set it themselves. Signature verification against
> Cloudflare's public keys, bound to this application's AUD, cannot be forged
> that way.

In **development** (`NODE_ENV !== 'production'`): the whole function returns
early, so auth, CSRF and rate limiting are all skipped. Local-only convenience.

---

## 7. Testing the Protection

### Verify it blocks unauthenticated requests

1. Open an **incognito window** (clears any CF Access cookie).
2. Visit `https://warroom.yishunagain.com`.
3. You should see the Cloudflare Access login screen — **not** the War Room UI.
4. Do **not** log in. Close the tab.

### Verify it blocks the wrong email

1. Log in to the Cloudflare Access screen with any email **other than**
   `OPERATOR_EMAIL` (you can use a disposable address), assuming the Access
   policy in §3 lets it through at all.
2. After Cloudflare grants the session, the War Room proxy will still return
   `403 Access denied` because the JWT's `email` claim doesn't match.

### Verify the correct email works

1. Log in with the address in `OPERATOR_EMAIL`.
2. The War Room queue page should load normally.

### Verify /api/health is NOT open

It used to be exempt. It is not any more — it returns fleet health and queue
counts, and the War Room has no bypass route.

```bash
curl -sS -o /dev/null -w '%{http_code}\n' https://warroom.yishunagain.com/api/health
# Expect the Cloudflare Access login redirect (302) — never 200.
```

Requests that reach Vercel without passing Cloudflare — the raw `*.vercel.app`
URL, for instance — hit the proxy instead, which answers `403` (no or invalid
JWT) or `503` (`CF_ACCESS_TEAM_DOMAIN` / `CF_ACCESS_AUD` missing on the
project). A `503` is the useful one: it says the gate is up and the config is
not.

---

## 8. What is NOT protected by this setup

- The **public site** (`yishunagain.com`) — intentionally open.
- The **agents backend** (`Cloud Run`) — protected by the `X-Ops-Token` shared
  secret on every `/ops` and `/art` endpoint (`hmac.compare_digest`; 503 if the
  server has no token configured, 401 if the caller's is wrong). Since
  2026-08-04 it is deployed `--allow-unauthenticated`, so that secret is the
  **only** gate and the service is reachable from the public internet. That is a
  deliberate trade: the GCP IAM layer it replaces was not protecting these
  endpoints, it was blocking the War Room's own calls — see §5a.
- Supabase direct access — blocked by RLS policies; the secret key is
  server-side only and never in client bundles.
