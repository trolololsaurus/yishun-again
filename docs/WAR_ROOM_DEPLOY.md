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
   - Value: `blyatimirovich.putin@gmail.com`
4. Click **Next**, then **Add application**.

This means only the above email can pass the Cloudflare login screen. Anyone
else (including you with a different email) is blocked at the edge before the
request reaches Vercel.

---

## 4. Create a Service Token (for agents backend)

The agents backend (Cloud Run) calls War Room APIs server-to-server without a
browser session. It authenticates using a service token instead.

1. Go to **Zero Trust → Access → Service Auth → Service Tokens**.
2. Click **Create Service Token**.
3. Name it `yishun-agents-backend`.
4. Copy the **Client ID** and **Client Secret** — the secret is only shown once.
5. Back in the War Room application, go to **Policies** and add a second policy:
   - Policy name: `Service token`
   - Action: **Service Auth**
   - Include rule: **Service Token** → `yishun-agents-backend`

Set the token values in Cloud Run env vars:

```
CF_ACCESS_CLIENT_ID=<client id from step 4>
CF_ACCESS_CLIENT_SECRET=<client secret from step 4>
```

When calling War Room from the agents backend, include these headers:

```python
headers = {
    "CF-Access-Client-Id":     os.environ["CF_ACCESS_CLIENT_ID"],
    "CF-Access-Client-Secret": os.environ["CF_ACCESS_CLIENT_SECRET"],
}
```

---

## 5. Environment Variables

Set these in the Vercel project for the `war-room` app (Production environment):

| Variable | Value | Notes |
|---|---|---|
| `SUPABASE_URL` | `https://xxxx.supabase.co` | From Supabase project settings |
| `SUPABASE_SECRET_KEY` | `eyJ...` | Service role key — bypasses RLS |
| `OPERATOR_EMAIL` | `blyatimirovich.putin@gmail.com` | Must be lowercase |
| `CF_ACCESS_CLIENT_ID` | *(from step 4)* | Used by agents backend only |
| `CF_ACCESS_CLIENT_SECRET` | *(from step 4)* | Used by agents backend only |
| `NEXT_PUBLIC_SITE_URL` | `https://www.yishunagain.com` | **www, not the apex** — see below |
| `WAR_ROOM_URL` | `https://warroom.yishunagain.com` | |
| `AGENTS_API_URL` | `https://yishun-agents-xxxxx-as.a.run.app` | Image generation (Track B) — see §5a |
| `OPS_TOKEN` | *(same value as Cloud Run)* | Byte-identical or every render 401s |
| `REVALIDATE_SECRET` | *(same value as the `web` project)* | Byte-identical or rectification silently no-ops |
| `ART_TIMEOUT_MS` | `120000` | Optional. Approve path — sized for the full retry ladder |
| `RECTIFY_TIMEOUT_MS` | `45000` | Optional. One attempt, no ladder |

`OPERATOR_EMAIL` is the second line of defence in `middleware.ts`: even if a
request somehow carries a valid CF Access header from a different email, the
middleware rejects it with 403. The primary defence is the Cloudflare policy
above.

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

### ⚠️ Blocked as deployed — `X-Ops-Token` is not enough

`yishun-agents` is deployed `--no-allow-unauthenticated`, and its only IAM
binding is `roles/run.invoker` for `yishun-scheduler@…`. Vercel has no identity
there, so Google's IAM layer answers **403 before the app ever reads the
header** — verified 2026-08-01 against the live service.

Until this is resolved every render silently returns `status: 'transient'`, the
incident publishes with `pixel_art_url` null, and the operator sees no error.
Two options:

1. **Make the service publicly invokable** and rely on `OPS_TOKEN` as the only
   gate — one command, but the token becomes the entire perimeter:
   ```
   gcloud run services add-iam-policy-binding yishun-agents      --region asia-southeast1 --member="allUsers" --role="roles/run.invoker"
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

`apps/war-room/middleware.ts` runs on every request except:
- `_next/static/*` — static asset chunks
- `_next/image/*` — image optimisation
- `favicon.ico`
- `/api/health` — health-check probe (always allowed)

In **production** (`NODE_ENV=production`):

1. Reads the `cf-access-authenticated-user-email` header injected by
   Cloudflare Access.
2. If the header is missing → `403 Access denied` (plain text).
3. If `OPERATOR_EMAIL` is set and the header value doesn't match → `403`.
4. Otherwise → request passes through to the Next.js app.

In **development** (`NODE_ENV=development`): check is skipped entirely.

---

## 7. Testing the Protection

### Verify it blocks unauthenticated requests

1. Open an **incognito window** (clears any CF Access cookie).
2. Visit `https://warroom.yishunagain.com`.
3. You should see the Cloudflare Access login screen — **not** the War Room UI.
4. Do **not** log in. Close the tab.

### Verify it blocks the wrong email

1. Log in to the Cloudflare Access screen with any email **other than**
   `blyatimirovich.putin@gmail.com` (you can use a disposable address).
2. After Cloudflare grants the session, the War Room middleware will still
   return `403 Access denied` because `OPERATOR_EMAIL` doesn't match.

### Verify the correct email works

1. Log in with `blyatimirovich.putin@gmail.com`.
2. The War Room queue page should load normally.

### Verify /api/health is always open

```bash
curl -I https://warroom.yishunagain.com/api/health
# Expect: HTTP/2 200  (no redirect to CF Access login)
```

---

## 8. What is NOT protected by this setup

- The **public site** (`yishunagain.com`) — intentionally open.
- The **agents backend** (`Cloud Run`) — protected separately by GCP IAM;
  it is not exposed to the public internet.
- Supabase direct access — blocked by RLS policies; the secret key is
  server-side only and never in client bundles.
