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
│   │   ├── orchestrator/   # Herald (milestone) agent
│   │   └── ops/            # Autonomy layer — see docs/AUTONOMY.md
│   │       ├── daily.py            # THE daily chain (Cloud Scheduler entry)
│   │       ├── activity.py         # agent_runs / agent_events logging
│   │       ├── notify.py           # operator email + dedup ledger
│   │       ├── auto_publish.py     # >=0.95 confidence gate
│   │       ├── learning_monitor.py # confidence/agreement deltas
│   │       ├── supervisor.py       # scraper fleet watchdog
│   │       ├── integrity.py        # dupes + hallucination checks
│   │       ├── maintenance.py      # plain-English failure digest
│   │       ├── backend_health.py   # component health + cost guard
│   │       └── monthly_report.py   # 30-day summary, 1st of month
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
```

**Tests are standalone scripts, not pytest modules.** Each `test_*.py` in
`packages/agents/` runs top-level assertions via a `check(name, cond)` helper and
ends in `raise SystemExit(1 if failed else 0)`. Running `pytest` on them fails at
collection (the module-level `SystemExit` aborts the run). Run them directly:

```bash
./.venv/Scripts/python.exe test_stage2_guardrails.py   # one file
for f in test_*.py; do ./.venv/Scripts/python.exe "$f" || echo "FAIL $f"; done
```

All are offline — no network, no API keys, no DB.

### Deployment

```bash
# Frontend
vercel deploy --prod

# Agents backend (Cloud Run, Singapore region)
gcloud run deploy yishun-agents --source packages/agents \
  --region asia-southeast1 --platform managed --no-allow-unauthenticated \
  --timeout=3600 --memory=1Gi --min-instances=0 --max-instances=2
```

`--timeout=3600` is required — a daily pass runs 5–20 min, well past the 300 s
default. `--min-instances=0` is the cost control (see `docs/AUTONOMY.md` §6).

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
| Stage 2 writer | Anthropic API | claude-haiku-4-5-20251001 (classify **and** write) |
| Orchestrator | LangGraph | 0.1.x |
| Image gen | Modal.run | SDXL + custom LoRA |
| Scheduling | APScheduler | 3.x (embedded in FastAPI) |
| CSS | Tailwind CSS | 3.x |

---

## Agent Pipeline

```
Scrape → Stage 1 (Gemini) → CLUSTER by story (one Haiku call) → Stage 2 Writer (Haiku)
                                                                          ↓
                                            groundedness + casualty cross-checks (deterministic)
                                                                          ↓
                                              Consolidation (ONE batched Haiku call) → war_room_queue
                                                                          ↓
                                            all gates clear AND confidence >= 0.95 ? ──yes──→ auto-publish
                                                                          ↓ no                     ↓
                                                     Operator reviews in War Room ←────────────────┘
                                                                          ↓         (training signal)
                                                            Approve → Art Agent → Publish
```

**Clustering runs BEFORE Stage 2, and is the reason drafts are written once per
STORY rather than once per URL.** `CLUSTER_BEFORE_WRITE=on` is wired and live (a
2026-07 `.env.example` comment claiming otherwise was stale). One batched Haiku
call partitions the pass's Stage-1-passed candidates; the keyword pass is only
the input filter that decides who is offered to it. See
`docs/PIPELINE_CHANGES_2026-07-30.md` §1 — in particular, **do not reintroduce
pairwise judging + union-find**: it merged transitively (A~B and B~C merged A, B
*and* C with nothing comparing A to C) and that is what produced the blob the
shadow run caught.

**Four things now hold a row back from auto-publish** beyond the confidence
threshold. All leave it `pending` for the operator; none reject anything:

| `check_eligibility` reason | Set by | Clears |
|---|---|---|
| `ungrounded_specifics` | Stage 2 groundedness post-check (regenerates once first) | Never automatically — a factual defect in that row |
| `casualty_mismatch` | `filters/casualty_check` — source language vs the model's deaths/injuries | Never automatically — same |
| `oversized_cluster_unproven` | A grouping call merged > `CLUSTER_MAX_SIZE` articles | **Automatically**, once the grouper earns it (`AUTONOMY.md` §5b) |
| `unapproved_source_domain` | `source_allowlist` | Operator approves the domain |

**The pipeline is autonomous as of July 2026.** One Cloud Scheduler job at
**14:58 SGT daily** POSTs `/orchestrator/daily`, which runs twelve agents in a
fixed order (`ops/daily.py`): recalibration → ingestion → auto-publish →
integrity → supervisor → learning monitor → backend health → pattern detection →
lifecycle (Mondays) → source discovery (first Monday) → maintenance digest →
monthly report (1st). Steps are failure-isolated: one agent crashing does not
cost you the rest.

**The cadence lives in `ops/daily.py` and nowhere else.** `main.py`'s in-process
APScheduler has exactly one job (the daily chain) and is off in production
anyway. Until 2026-07-30 it also carried pattern detection, recalibration,
lifecycle and discovery — which meant those four had **never run in production**,
because Cloud Run scales to zero and that scheduler never starts. Do not add a
second scheduling surface; add a cadence-gated step to `ops/daily.py` and a case
to `cadence_plan()`. Two things worth knowing:
- **Recalibration must stay ahead of ingestion.** It writes `calibration_log.json`
  on an ephemeral disk and Stage 2 reads it while drafting *in the same pass*.
  Moved below ingestion, the calibration loop becomes a silent no-op.
- **`dry_run` skips every cadence-gated step.** None of them has a read-only mode.

**Lifecycle auto-conclude is wired but OFF** (`LIFECYCLE_AUTO_CONCLUDE`). It is
the only agent that edits an already-published incident unattended, so enabling
it is an operator decision — see `docs/AUTONOMY.md` §5d.

**Read `docs/AUTONOMY.md` before changing any of this.** It documents the
auto-publish gates, the alert throttling, the exit conditions, the cost model,
and the runbook (including the `AUTO_PUBLISH_CONFIDENCE=2.0` panic switch).
**§5b** is the oversized-merge gate that lifts itself once earned; **§5c** is the
`max_tokens` truncation guard and its recovery ladder; **§5d** is the lifecycle
switch.

**Read `docs/PIPELINE_CHANGES_2026-07-30.md` before touching clustering,
consolidation, Stage 2 length/groundedness, or the casualty check.** It records
what was measured, and — more usefully — the three things in the original brief
that turned out to be wrong about this codebase, so they are not re-attempted.

Two things that are easy to get wrong:
- **Production does NOT use APScheduler.** `ENABLE_INPROCESS_SCHEDULER` is false.
  Cloud Run scales to zero, so an in-process scheduler simply does not fire
  unless you pay ~$15-25/mo for `min-instances=1` + CPU-always-allocated.
- **`ops/` must never raise.** Every module there swallows its own exceptions and
  degrades to stdlib logging. An observability layer that can crash the pipeline
  it observes turns a logging outage into a data outage.

**Stage 1 (Gemini):** Fast noise rejection. Pass threshold: confidence ≥ 0.4. Target 60–70% rejection of raw scrape volume.

**Stage 2 (Claude Haiku):** Classification, draft writing, severity scoring. Returns
JSON — see spec §4.3 for the schema, with three deltas since:

- **`pixel_art_prompt` is no longer generated.** The War Room approve route
  hardcodes `pixel_art_url: null`, so it was written on every draft and read by
  nothing. The DB column and `pixel_art_url` are untouched — art generation is
  dormant by design, not deleted.
- **The write model is Haiku**, not Sonnet (`STAGE2_WRITE_MODEL` to roll back).
  Justified by an eval over 30 real inputs; Haiku matched Sonnet on ungrounded
  specifics on the multi-source half and on format compliance.
- **Summary length is arithmetic, not an instruction.** `min(1600, RATIO x
  non-signal source chars)`, floored at 400, interpolated into the prompt as a
  hard number. The prose "~1600 char" ceiling was measurably ignored — Sonnet
  exceeded it on 10 of 29 eval drafts, worst 2765.

**Signal treatment (EDMW *and* Reddit):** a signal is never a quoted source and
never the event date. Reddit joined this tier in July 2026 (was `'reddit'`) — it
is user-generated discussion, not verifiable journalism, and its post date is not
an event date (a thread reviving an old case carries a recent post date, which
manufactured duplicate cards for old events). MSM is the sole authority for both
the citation and the date. Three tiers:
- Signal only → stays in the queue as unverified until an operator attaches an MSM source; never auto-publishes (signal URL is never in `source_urls` per guardrail #2, so guardrail #1's ≥1-source requirement is unmet)
- Signal + MSM corroboration → standard incident; the signal count shows as "Forum buzz"
- MSM only → standard incident, no signal reference

**Scraping:** 15 sources are wired into the live pipeline via `ingestion/sources/`
(`get_enabled_sources()`): **RSS-dated MSM** — CNA, Mothership, Straits Times,
MustShareNews, The Independent, Yahoo; **HTML-scraped MSM** — AsiaOne, Stomp,
Zaobao, Shin Min, Berita Harian, Tamil Murasu, whose listing pages carry no date,
so `scrapers.resolve_published_at()` reads it from the article (URL path, else
meta tags); **corroboration** — Google News RSS, the main discovery channel in
practice; **signal** — Reddit (r/singapore, r/singaporeraw) and EDMW/HWZ.

A source must supply `published_at` to be registered: a dateless candidate
bypasses the recency watermark, is re-processed by Stage 1/2 every pass, and
can't be approved until an operator sets the date by hand (QA H3).

**EDMW/HWZ and Reddit are registered as `source_type='signal'`** (EDMW Phase 3,
commit `522e09d`; Reddit July 2026). Guardrail #2 is enforced in `orchestrator.py`
via `source_allowlist.is_signal_source()` — never a plain `== 'edmw'`, because
`scrape_edmw` and `scrape_reddit` both emit the canonical `'signal'` and that vocabulary mismatch
silently breached the guardrail once already (`92d6305`).

`google_news_rss` filters candidates on the RSS entry's own `pubDate` **before**
resolving Google redirect wrappers. Resolving first meant ~600 wasted HTTP
round-trips per pass on entries the recency filter then discarded — that alone
consumed the entire pass budget and starved the sources queued behind it
(909 s → 59 s). `MAX_RESOLVES_PER_FETCH` caps a cold start.

Scrapers **raise** `ScraperError`/`ScraperBlocked` on a source-level failure
rather than returning `[]`; the adapters translate those to
`SourceBlockedError`/`SourceUnavailableError`. An empty result therefore means
"no Yishun news", not "something broke quietly" — Stomp sat silently dead for
weeks under the old behaviour.

**`scrapers.scrape_all()` is not on any live path** and neither is
`log_scraper_run()`, which only it calls — so nothing writes `scraper_health`.
`ops/supervisor.py` used to read that table for its zero-streak check, making
that check permanently dead; it now derives the streak from
`pipeline_run_history` instead. Do not build new monitoring on `scraper_health`
without first giving it a writer.

---

## Database

Supabase, `public` schema. RLS enabled on all tables — public reads only, all writes via `SUPABASE_SECRET_KEY` from agents backend only.

Key tables: `incidents`, `sources`, `war_room_queue`, `utm_events`, `training_signals`, `chaos_index_snapshots`

Full schema with exact SQL: `docs/YishunAgain_TechSpec_v1_9.md` §3.

**Sources seed data** (16 rows) is in the spec — run as part of Step 1 migration.
The live table has since grown to **43**: the 16 seeded scrape targets plus 26
citation-only domains approved after the July-2026 allowlist audit (government
and court records, SG media, foreign outlets). Citation-only rows are
`type='msm'`, `is_active=false`, `scrape_interval_minutes=0` — quotable but never
scraped. `type='msm'` rather than `'reference'` is deliberate:
`backfill_agent.py` excludes `reference` URLs from `source_urls`, which would
silently drop court judgments and police releases as citations.

**Source allowlist** (`classifiers/source_allowlist.py`): every `source_url` is
checked against this table. A `type='signal'` domain is **removed** (guardrail
#2); a domain that is unknown or not `approved_by_operator` is **kept and
flagged** in `raw_content._source_allowlist` for operator review — stripping it
could take an incident's last source and break guardrail #1. Matching is
suffix-aware, so `cnalifestyle.channelnewsasia.com` inherits CNA's approval.

**Migrations are hand-applied in the Supabase SQL Editor (no runner).** Apply in
order; the live DB depends on `006_phase1_apply_now.sql` + `007` + `009` having all
run. `006_ingestion_learning_loop_schema.sql` is a **superseded draft — do not run.**
Recent additions: **008** expands `incidents.latest_source_role` to include
`sentencing` / `appeal` / `appeal_dismissed`; **009** adds `unpublish` to the
`training_signals.action` CHECK (the War Room unpublish route writes it — before 009
those inserts were silently rejected). The lack of a migration runner is tracked as
QA M15.

**011 (autonomy/ops)** adds `agent_runs`, `agent_events`, `notifications`,
`learning_snapshots`, `monthly_reports`, `backend_health_checks`; extends
`training_signals.action` with `auto_approve` / `auto_publish_reverted` and adds
`decided_by` (`operator` | `agent`); and retires the Jom seed row. It also
backfills `CREATE TABLE IF NOT EXISTS` for **`scraper_health` and `milestones`**,
which existed in the live DB but only as DDL inside the TechSpec — no migration
file had ever captured them, so a rebuild-from-migrations was impossible.

⚠️ Same failure mode as 009: without 011's CHECK update, every autonomous
decision insert is silently rejected and the learning loop records nothing.
The agents degrade gracefully when the ops tables are missing (they log to
stdout and continue) — so a missing migration looks like silence, not an error.

**012 (reddit as signal)** flips the two reddit rows in `sources` to
`type='signal'`. Reddit was reclassified from a quoted source to a signal (UGC,
not journalism; post date ≠ event date). The code change (`scrape_reddit` emits
`'signal'`) is what enforces it in the pipeline; 012 is the defensive layer so
`classify()` resolves reddit domains to signal too. Not required for the code
path to work.

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
2. Sources with `type = 'signal'` (EDMW/HWZ **and Reddit**) are never included in `source_urls`.
3. No personal information beyond what appears in public source URLs.
4. If Stage 2 detects political content → set `confidence = 0`, flag `"[POLITICAL CONTENT DETECTED — REJECT]"`.

> ✅ **Enforcement status (verified 2026-07-30 against the code — see
> `docs/PIPELINE_CHANGES_2026-07-30.md`).** The June-2026 QA sweep found #1, #2
> and #4 unenforced. **All three have since landed**; this block previously still
> said they were open, which is why it is dated now:
> - **#1** — closed by **migration 010**: `CHECK (cardinality(source_urls) >= 1)`.
>   The old `array_length('{}',1)` returned NULL and let `'{}'` pass (QA C4).
> - **#2** — closed in `ingestion/orchestrator.py`: a signal candidate gets
>   `source_urls=[]` and carries only `edmw_signal_count`. Enforced via
>   `source_allowlist.is_signal_source()`, **never** a bare `== 'edmw'` (QA C2).
> - **#4** — closed in `filters/stage2_writer.py::_classify`: `political: true`
>   forces `confidence = 0.0` before the merge, and `write_stage2` prepends the
>   operator-visible reject marker (QA C1). Since 2026-07-30 it also **alerts** —
>   marker, operator email and a `warning` `agent_events` row — because a
>   silently-zeroed row was indistinguishable from any other low-confidence row.
> - **#3** still has no programmatic check — operator-gate only.
>
> Regression guards: `test_stage2_guardrails.py`, `test_political_alert.py`,
> `test_source_allowlist.py`. Strengthen these freely; never weaken them.

---

## Frontend Theme

Dark pixel art retro tabloid. Two fonts only: `Press Start 2P` (headers, scores, logo) and `Courier Prime` (all body text). Three sizes max: 24–28px, 11–12px, 8–10px. Two weights: 400 and 700.

CSS tokens are defined in spec §6.1. Key colours: bg `#0D0D0D`, accent red `#E74C3C`, accent yellow `#F1C40F`, dagger purple `#8E44AD`.

Map: MapLibre GL JS with OpenFreeMap "Liberty" style (`https://tiles.openfreemap.org/styles/liberty`). Keyless — no Mapbox token, no Stadia/CartoDB. `IncidentMap.tsx` reads `NEXT_PUBLIC_MAPLIBRE_STYLE` with a hardcoded fallback to the same Liberty URL (`||`, so an empty-string env var also falls back), so the map can never be a single point of failure if the var is unset. A set-but-wrong env var overrides the fallback. Because `NEXT_PUBLIC_*` vars are baked at build time, changing it requires a fresh deploy, not just a restart.

**Lightning (⚡) = corroboration, not a separate hype field.** As of the June-2026 feed pass, the lightning meter is derived live from `corroboration_count`: `bolts = max(0, corroboration_count − 1)` (2 sources → ⚡, 3 → ⚡⚡, …). It grows as sources merge into one incident. The legacy `hype_meter` column is no longer read by the frontend. The **DEVELOPING** badge/banner was removed (it confused readers); `is_developing` drives the report-count line only — the feed is sorted newest-first (`incident_date DESC`, `id` tiebreaker), not by `is_developing`. The story timeline collapses same-date entries to a single node, and "time to verdict" is computed from the last verdict/sentencing/appeal entry in `source_timeline` (never `incident_date`). See `docs/FRONTEND_SPEC.md` and `lib/utils.ts` (`hypeFromSources`, `lastVerdictEntry`, `collapseTimelineByDate`).

Share cards: rendered via OG meta tags — no separate image generation. The pixel art image (already generated for incident page) doubles as the OG image.

---

## Chaos Index

Computed on every new publish, stored in `chaos_index_snapshots`.

**Per-incident points** (unchanged — these are what Stage 2 stores as
`chaos_contribution`):
- Dagger: `severity × 3.0`
- Clown: `severity × 1.5`
- Heart: `severity × -1.0`

**Aggregate score** (`computeChaosScore`, `apps/web/lib/utils.ts` — the only
place it is calculated; `/api/chaos` and the SSR homepage both use it):

```
raw   = Σ (severity × weight) for the selected year, floored at 0
score = round(100 × (1 − e^(−raw / CHAOS_SCALE)))      CHAOS_SCALE = 300
```

Rebalanced July 2026. It was `min(100, raw / 300 × 100)` — linear with a hard
cliff, so 20 severity-5 daggers (raw 300) pegged a year at 100 permanently and
2026 read **87 Apocalyptic by July**. Because `raw` is a cumulative sum over the
year it only ever climbed, making the index closer to "how much have we
catalogued" than "how chaotic was it". The curve now gives diminishing returns
and approaches 100 asymptotically without reaching it: raw 300 → 63, and
Apocalyptic (≥80) needs raw ≈ 483, about 32 severity-5 daggers in one year.
2026 reads **58 Elevated** with room to grow.

Descriptors: Quiet / Simmering / Elevated / Critical / Apocalyptic (thresholds: 0/20/40/60/80).

⚠️ The index still tracks **archive coverage** as much as reality — thin
historical years read Quiet because few incidents are catalogued, not because
Yishun was calm. Comparing years is therefore not apples-to-apples.

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
