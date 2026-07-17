# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Yishun Again is a satirical, semi-autonomous incident archive for Yishun, Singapore. An AI agent pipeline scrapes sources, drafts incident write-ups, and queues them for operator review in a private CMS (War Room). The operator approves, edits, or rejects each draft before it goes live.

**Core constraint:** Every published incident must link to a verifiable source. No private individuals unless named in MSM or Reddit. No political content. Ever.

Full spec: `docs/YishunAgain_TechSpec_v1_9.md`

---

## Repository Structure

```
yishun-again/
├── apps/
│   ├── web/          # Next.js 14 (App Router) — public site, Vercel deploy
│   └── war-room/     # Next.js 14 (App Router) — private operator CMS
├── packages/
│   ├── agents/       # FastAPI 0.110.x + Python 3.11 agent pipeline
│   │   ├── scrapers/       # Per-source scraping agents (RSS-first)
│   │   ├── filters/        # Stage 1 (Gemini) + Stage 2 (Claude) filters
│   │   ├── classifiers/    # Corroboration + severity scoring
│   │   ├── writers/        # Incident draft generation
│   │   ├── art/            # Pixel art prompt gen + Modal.run calls
│   │   ├── cards/          # Share card generation
│   │   └── orchestrator/   # LangGraph 0.1.x orchestrator
│   ├── db/           # Supabase schema, migrations, types
│   └── shared/       # Shared types, constants, utils
└── infra/
    ├── cloudbuild.yaml
    └── cloudflare/
```

---

## Common Commands

### Frontend (`apps/web/`, `apps/war-room/`)

```bash
npm run dev         # Start dev server
npm run build       # Production build
npm run lint        # ESLint
npm audit           # Dependency security check (run before deploy)
```

### Agents backend (`packages/agents/`)

```bash
pip install -r requirements.txt
uvicorn main:app --reload --port 8080   # Dev server
pytest                                   # Run tests
pytest tests/test_stage1.py             # Run single test file
```

### Deployment

```bash
# Frontend
vercel deploy --prod

# Agents backend (Cloud Run, Singapore region)
gcloud run deploy yishun-agents --source . --region asia-southeast1 --platform managed
```

---

## Build Order (Phase 1)

Execute strictly in sequence — do not skip ahead:

1. Supabase schema — all tables, indexes, RLS policies
2. Cloudflare R2 bucket + `assets.yishunagain.com` domain
3. FastAPI skeleton on Cloud Run — health check only
4. Stage 1 filter (Gemini) — unit test with sample content
5. Stage 2 writer (Claude) — unit test with sample content
6. Scraping agents — CNA + Mothership first
7. War Room CMS — queue view + approve/reject flow
8. Training signal logging
9. Next.js frontend — map + Chaos Index (static mock data first)
10. Wire frontend to Supabase
11. Share card generation + UTM logging
12. Art pipeline (Modal.run + LoRA)
13. SEO meta tags, sitemap, schema markup
14. Cloudflare Access for War Room
15. Historical incident backfill
16. Launch

---

## Tech Stack

| Layer | Tool | Version |
|---|---|---|
| Frontend | Next.js App Router | 14.x |
| Map | MapLibre GL JS | 3.x |
| Database | Supabase (Postgres + REST) | Latest |
| Image storage | Cloudflare R2 | — |
| Admin auth | Cloudflare Access | Free tier |
| Backend | FastAPI | 0.110.x / Python 3.11+ |
| Agent hosting | Google Cloud Run | asia-southeast1 |
| Stage 1 filter | Gemini API | gemini-3.1-flash-lite |
| Stage 2 writer | Anthropic API | claude-haiku-4-5-20251001 (classify), claude-sonnet-4-6 (write) |
| Orchestrator | LangGraph | 0.1.x |
| Image gen | Modal.run | SDXL + custom LoRA |
| Scheduling | APScheduler | 3.x (embedded in FastAPI) |
| CSS | Tailwind CSS | 3.x |

---

## Agent Pipeline

```
Scrape Agent → Stage 1 Filter (Gemini) → Stage 2 Writer (Claude) → Corroboration Agent → war_room_queue
                                                                                              ↓
                                                                               Operator reviews in War Room
                                                                                              ↓
                                                                               Approve → Art Agent → Publish
```

**Stage 1 (Gemini):** Fast noise rejection. Pass threshold: confidence ≥ 0.4. Target 60–70% rejection of raw scrape volume.

**Stage 2 (Claude):** Classification, draft writing, severity scoring, pixel art prompt generation. Returns JSON — see spec §4.3 for exact schema and system prompts.

**EDMW treatment:** EDMW signal is never a quoted source. Three tiers:
- EDMW only → publishable with 👎 "unverified" badge, no lightning (corroboration_count = 0/1; the EDMW URL is never in `source_urls` per guardrail #2)
- EDMW + MSM corroboration → standard incident, EDMW count shown as "Forum buzz"
- MSM only → standard incident, no EDMW reference

**Scraping:** RSS-first for CNA/Mothership. Reddit via public JSON API (`/search.json?q=yishun&sort=new`). HWZ HTML scraping — title + thread stats only, never quote content.

---

## Database

Supabase, `public` schema. RLS enabled on all tables — public reads only, all writes via `SUPABASE_SECRET_KEY` from agents backend only.

Key tables: `incidents`, `sources`, `war_room_queue`, `utm_events`, `training_signals`, `chaos_index_snapshots`

Full schema with exact SQL: `docs/YishunAgain_TechSpec_v1_9.md` §3.

**Sources seed data** (18 sources) is in the spec — run as part of Step 1 migration.

**Migrations are hand-applied in the Supabase SQL Editor (no runner).** Apply in
order; the live DB depends on `006_phase1_apply_now.sql` + `007` + `009` having all
run. `006_ingestion_learning_loop_schema.sql` is a **superseded draft — do not run.**
Recent additions: **008** expands `incidents.latest_source_role` to include
`sentencing` / `appeal` / `appeal_dismissed`; **009** adds `unpublish` to the
`training_signals.action` CHECK (the War Room unpublish route writes it — before 009
those inserts were silently rejected). The lack of a migration runner is tracked as
QA M15.

**RLS note:** `incidents` anon reads are filtered to `is_published = TRUE`
(`anon_read_published_incidents`), so the **publishable key cannot see drafts at all** —
only the War Room (secret key) can. Any read-only audit run with the anon key will
under-count by the number of unpublished drafts.

---

## Security Constraints

- **Never call Supabase directly from React components.** All DB access goes through Next.js API routes (`/api/*`). `SUPABASE_SECRET_KEY` is server-side only.
- `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` and `NEXT_PUBLIC_MAPLIBRE_STYLE` are the only env vars safe to expose to the browser.
- All API route query params must be sanitised before hitting Supabase (slug: alphanumeric+hyphens only; ID: UUID regex).
- Rate limit all `/api/*` routes (60 req/min per IP for Phase 1).
- `next.config.js` must include the exact security headers block from spec §10b.2 (CSP, HSTS, X-Frame-Options, etc.).
- War Room (`warroom.yishunagain.com`) is never accessible without Cloudflare Access auth. No bypass route.
- `utm_events` stores no IP addresses, no cookies, no persistent user identifiers — only hashed user agent (SHA256[:16]) and Cloudflare geo headers.

---

## Legal Guardrails (Hardcoded — Never Remove)

1. `source_urls` must contain ≥ 1 URL — database constraint enforces this.
2. Sources with `type = 'signal'` (EDMW) are never included in `source_urls`.
3. No personal information beyond what appears in public source URLs.
4. If Stage 2 detects political content → set `confidence = 0`, flag `"[POLITICAL CONTENT DETECTED — REJECT]"`.

> ⚠️ **Enforcement status (June-2026 QA — see `docs/QA_BACKLOG.md`).** These four are
> the intended invariants and must never be weakened, but the QA sweep found the
> automated enforcement is incomplete:
> - **#1** the DB `CHECK (array_length(source_urls,1) >= 1)` does **not** reject an
>   empty array (`array_length('{}',1)` is NULL) — fix to `cardinality(...) >= 1` (QA C4).
> - **#2** the ingestion orchestrator currently puts an `edmw` candidate's URL into
>   `source_urls` (latent — no EDMW adapter yet) (QA C2).
> - **#4** Stage 2 only *logs* the marker; it never zeroes confidence or rejects (QA C1).
> - **#3** has no programmatic check — operator-gate only.
> Closing C1–C4 is the top priority in the QA backlog.

---

## Frontend Theme

Dark pixel art retro tabloid. Two fonts only: `Press Start 2P` (headers, scores, logo) and `Courier Prime` (all body text). Three sizes max: 24–28px, 11–12px, 8–10px. Two weights: 400 and 700.

CSS tokens are defined in spec §6.1. Key colours: bg `#0D0D0D`, accent red `#E74C3C`, accent yellow `#F1C40F`, dagger purple `#8E44AD`.

Map: MapLibre GL JS with OpenFreeMap "Liberty" style (`https://tiles.openfreemap.org/styles/liberty`). Keyless — no Mapbox token, no Stadia/CartoDB. `IncidentMap.tsx` reads `NEXT_PUBLIC_MAPLIBRE_STYLE` with a hardcoded fallback to the same Liberty URL (`||`, so an empty-string env var also falls back), so the map can never be a single point of failure if the var is unset. A set-but-wrong env var overrides the fallback. Because `NEXT_PUBLIC_*` vars are baked at build time, changing it requires a fresh deploy, not just a restart.

**Lightning (⚡) = corroboration, not a separate hype field.** As of the June-2026 feed pass, the lightning meter is derived live from `corroboration_count`: `bolts = max(0, corroboration_count − 1)` (2 sources → ⚡, 3 → ⚡⚡, …). It grows as sources merge into one incident. The legacy `hype_meter` column is no longer read by the frontend. The **DEVELOPING** badge/banner was removed (it confused readers); `is_developing` still drives feed sort + the report-count line. The story timeline collapses same-date entries to a single node, and "time to verdict" is computed from the last verdict/sentencing/appeal entry in `source_timeline` (never `incident_date`). See `docs/FRONTEND_SPEC.md` and `lib/utils.ts` (`hypeFromSources`, `lastVerdictEntry`, `collapseTimelineByDate`).

Share cards: rendered via OG meta tags — no separate image generation. The pixel art image (already generated for incident page) doubles as the OG image.

---

## Chaos Index

Computed on every new publish, stored in `chaos_index_snapshots`:
- Dagger: `severity × 3.0`
- Clown: `severity × 1.5`
- Heart: `severity × -1.0`
- Normalised to 0–100 (max theoretical: 300 raw points → 100)

Descriptors: Quiet / Simmering / Elevated / Critical / Apocalyptic (thresholds: 0/20/40/60/80).

---

## Environment Variables

Required env vars (never commit actual values):

```
SUPABASE_URL
SUPABASE_PUBLISHABLE_KEY   # frontend-safe
SUPABASE_SECRET_KEY        # server/agents only — bypasses RLS
ANTHROPIC_API_KEY
GEMINI_API_KEY
NEXT_PUBLIC_MAPLIBRE_STYLE
CF_R2_ACCOUNT_ID, CF_R2_ACCESS_KEY_ID, CF_R2_SECRET_ACCESS_KEY, CF_R2_BUCKET_NAME
MODAL_TOKEN_ID, MODAL_TOKEN_SECRET
NEXT_PUBLIC_SITE_URL
WAR_ROOM_URL
```

`SUPABASE_SECRET_KEY` and API keys go in Google Cloud Run env vars — never in `.env` files committed to the repo.

---

## What Is Not Being Built (Phase 1)

No user accounts, comments, votes, TikTok pipeline, distribution orchestrator, monetisation, mobile app, admin user roles, or public API.
