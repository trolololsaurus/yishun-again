# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Yishun Again is a satirical, semi-autonomous incident archive for Yishun, Singapore. An AI agent pipeline scrapes sources, drafts incident write-ups, and queues them for operator review in a private CMS (War Room). The operator approves, edits, or rejects each draft before it goes live.

**Core constraint:** Every published incident must link to a verifiable source,
and that link must point at the **publisher** — never an aggregator or a redirect
wrapper. No private individuals unless named in MSM. No political content. Ever.

(Reddit used to appear in that second sentence alongside MSM. It was reclassified
as a `signal` in July 2026 — user-generated discussion is not verifiable
journalism — so it can no longer name anyone. MSM is the sole authority for both
the citation and the event date.)

Full spec: `docs/YishunAgain_TechSpec_v1_9.md`

> **On documentation drift.** This file and `docs/` are read by agents as fact.
> A stale claim here does not merely mislead — it gets acted on. Two lived
> examples: every TechSpec from v1.5 said `sembawang` had been removed from the
> Yishun keywords while the code still contained it, and this file described art
> generation as "dormant by design" while it was running on the operator approve
> path. **If you change behaviour, change the doc in the same commit.** If you
> find a claim you cannot verify against the code, delete it rather than softening
> it — an unverifiable claim is worse than a missing one.

---

## Repository Structure

```
yishun-again/
├── apps/
│   ├── web/          # Next.js 16 (App Router) — public site, Vercel deploy
│   └── war-room/     # Next.js 16 (App Router) — private operator CMS
├── packages/
│   ├── agents/       # FastAPI 0.115.x + Python 3.11 agent pipeline
│   │   ├── scrapers/       # Per-source scraping agents (RSS-first)
│   │   ├── ingestion/      # Live pass: sources, recency, dedup, health
│   │   │   └── sources/    # Source adapters — get_enabled_sources()
│   │   ├── filters/        # Stage 1 (Gemini) + Stage 2 (Claude) filters
│   │   ├── classifiers/    # Corroboration + severity scoring
│   │   ├── writers/        # Incident draft generation
│   │   ├── art/            # Scene writer + Gemini image gen + R2 upload
│   │   ├── cards/          # Deleted — share cards are OG meta tags (no code)
│   │   ├── orchestrator/   # Herald (milestone) agent
│   │   └── ops/            # Autonomy layer — see docs/AUTONOMY.md
│   │       ├── daily.py            # THE daily chain (Cloud Scheduler entry)
│   │       ├── activity.py         # agent_runs / agent_events logging
│   │       ├── notify.py           # operator alerting (Telegram) + dedup ledger
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

All are offline — no network, no API keys, no DB. There are **45 test files** and
they all pass as of 2026-08-29; a red file is a real regression, not a flake.

The web app has tests too, added 2026-08-04 — `apps/web/lib/utils.test.ts`, run
with `npm test` from `apps/web`. There is no test framework installed: it uses
`node:test` plus Node 24's native TypeScript stripping, which is why its import
of `./utils.ts` carries the extension. It covers the three pure helpers that
decide what an incident page *says* (`sharedLocationLabel`, `dateFromUrl`,
`toParagraphs`), where a silent break is a factual error on a published page.

The War Room has three of its own, same runner, no `npm test` script — invoke
them directly from the repo root:

```bash
node --test apps/war-room/lib/utils.paragraphs.test.ts apps/war-room/lib/utils.incidentRef.test.ts apps/war-room/lib/utils.updateMerge.test.ts
```

`utils.paragraphs.test.ts` is a PARITY guard: it reads both `lib/utils.ts`
files and asserts the ported blocks are byte-identical, because the War Room
duplicates the web app's paragraph splitting **and** its
`canonicalUrl`/`uniqueSources` source counting (there is no `packages/shared`
wired into either app). Change one copy and it goes red — change both or
neither. `utils.incidentRef.test.ts` covers `/rectify`'s URL lookup box.
`utils.updateMerge.test.ts` guards `applyUpdate`/`revertUpdate` — an applied
update and its undo must round-trip exactly (`revert(apply(x)) == x`); its Python
mirror is `_compute_merge` in `ops/auto_publish.py`, guarded by
`test_auto_merge_eligibility.py`.

Note for Windows: the console codepage is cp1252, so a `check()` label containing
CJK or Tamil raises `UnicodeEncodeError` before the assertion result prints. Keep
test *names* ASCII even when the strings under test are not.

### Deployment

```bash
# Frontend
vercel deploy --prod

# Agents backend (Cloud Run, Singapore region)
gcloud run deploy yishun-agents --source packages/agents \
  --region asia-southeast1 --platform managed --allow-unauthenticated \
  --timeout=3600 --memory=1Gi --min-instances=0 --max-instances=2
```

`--timeout=3600` is required — a daily pass runs 5–20 min, well past the 300 s
default. `--min-instances=0` is the cost control (see `docs/AUTONOMY.md` §6).

**`--allow-unauthenticated` is deliberate, and the auth is `OPS_TOKEN`.** This
said `--no-allow-unauthenticated` until 2026-08-04, and that flag silently broke
the art pipeline for its entire life. Cloud Run IAM and the app are two
independent gates: IAM wants a Google-signed OIDC token in `Authorization`,
`main.py::_require_ops_token` wants `X-Ops-Token`. Cloud Scheduler satisfies both
(it runs as `yishun-scheduler@…`, still bound as an invoker), but the War Room is
on **Vercel, not GCP**, and `lib/artGenerate.ts` sends only `X-Ops-Token`. Every
`/art/generate` call was rejected at the edge with
`403 … Empty Authorization header value` and never reached FastAPI — which is why
`image_prompt` was NULL on all 172 incidents and the archive held exactly one
image. Every route except `/health` is behind `_require_ops_token`
(`hmac.compare_digest`, 503 if the server has no token), so the shared secret is
the gate now. **Do not redeploy with `--no-allow-unauthenticated`** unless you
are also giving the War Room a way to mint an OIDC token — you will re-break art
generation, and the only symptom is a queue of `transient` rows with empty
prompt boxes.

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
12. Art pipeline (Gemini image API — the original Modal.run + LoRA build was torn down July 2026)
13. SEO meta tags, sitemap, schema markup
14. Cloudflare Access for War Room
15. Historical incident backfill
16. Launch

---

## Tech Stack

Versions below are the actual pins (`apps/*/package.json`,
`packages/agents/requirements.txt`), not aspirations. Check there before
trusting this table.

| Layer | Tool | Version |
|---|---|---|
| Frontend | Next.js App Router | 16.2.x (React 19.2.x) |
| Map | MapLibre GL JS | 3.x |
| Database | Supabase (Postgres + REST) | Latest |
| Image storage | Cloudflare R2 | — |
| Admin auth | Cloudflare Access | Free tier |
| Backend | FastAPI | 0.115.14 / Python 3.11 |
| Agent hosting | Google Cloud Run | asia-southeast1 |
| Stage 1 filter | Gemini API | gemini-3.1-flash-lite |
| Stage 2 writer | Anthropic API | claude-haiku-4-5-20251001 (classify **and** write) |
| Orchestrator | *hand-rolled* — see below | — |
| Image gen | Gemini image API | gemini-3.1-flash-lite-image |
| Scheduling | Cloud Scheduler (POSTs `/orchestrator/daily`) | — |
| CSS | Tailwind CSS | 3.x |

**There is no LangGraph orchestrator.** `langgraph` has been removed from
`requirements.txt` (2026-08-24 — it was never imported). Orchestration is
hand-rolled in `ops/daily.py` (the daily chain) and `ingestion/orchestrator.py`
(the pass). Do not write code that assumes a graph runtime.

FastAPI is pinned to 0.115.14 rather than the 0.110.x this table used to name —
bumped for the starlette CVE-2024-47874 fix.

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

**Auto-MERGE of `update` rows is a separate, opt-in gate** (`AUTONOMY.md` §2b),
off by default (`AUTO_MERGE_ENABLED`). An `update` row applies a new source to an
already-published incident; when enabled it auto-applies only when BOTH the draft
confidence AND the consolidation `_match_confidence` clear 0.95 and the appended
source survives the allowlist. It snapshots the pre-merge state
(`raw_content._undo_snapshot`) so the operator can undo it from the War Room
queue's "Recently merged updates" panel (`/api/queue/[id]/revert-update`).
Migration 018 is required. `check_update_eligibility` / `_apply_merge` in
`ops/auto_publish.py`; the merge math (`_compute_merge`) mirrors `applyUpdate` in
`apps/war-room/lib/utils.ts`.

**Every `update` row carries an ENRICHED summary** — the target incident's summary
refreshed with the new development, generated at ingestion by
`consolidation/enrich.py` (one Haiku call, screened by Stage 2's `find_ungrounded`).
The War Room `UpdateCard` pre-fills its box with it for operator review; the
autonomous merge applies it only when `AUTO_ENRICH_SUMMARY` is on AND it passed
groundedness (both off/gated by default, `AUTONOMY.md` §2b–2c). Fails safe — any
problem leaves the existing summary. This replaces the old blank-box behaviour: the
box used to pre-fill with the new source's terse *standalone* draft, which
wholesale-replaced the full summary on confirm (`applyUpdate` only keeps-or-replaces
prose, never merges it) — the mechanism that corrupted a live incident via a Reddit
merge. Guard: `test_summary_enrichment.py`.

**The pipeline is autonomous as of July 2026.** One Cloud Scheduler job runs
**twice daily at 02:58 and 14:58 SGT** and POSTs `/orchestrator/daily`, which runs twelve agents in a
fixed order (`ops/daily.py`): recalibration → ingestion → auto-publish →
integrity → supervisor → learning monitor → backend health → pattern detection →
lifecycle (Mondays) → source discovery (first Monday) → maintenance digest →
monthly report (1st). Steps are failure-isolated: one agent crashing does not
cost you the rest.

**The cadence lives in `ops/daily.py` and nowhere else.** There is no in-process
scheduler: `main.py` used to carry a single-job APScheduler behind
`ENABLE_INPROCESS_SCHEDULER`, off in production and **removed 2026-08-29** as
redundant with `POST /orchestrator/daily` (and APScheduler dropped as a
dependency). Until 2026-07-30 that scheduler also carried pattern detection,
recalibration, lifecycle and discovery — which meant those four had **never run
in production**, because Cloud Run scales to zero and it never started. Do not add
a second scheduling surface; add a cadence-gated step to `ops/daily.py` and a case
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

**The recency watermark advances on DECISIONS, not on writes.** A Stage 1
rejection and a consolidation duplicate-skip are verdicts, and neither writes a
row, so `dedup.is_duplicate` (which reads only `war_room_queue.source_url` and
`incidents.source_urls`) can never see them again — the watermark is the only
thing that can. Each source gets a `WatermarkTracker` (`ingestion/watermark.py`)
and **every `continue`/`break` in the candidate loop must mark it exactly once**:
`decided()` for a verdict, `unresolved()` for an interruption (error, deadline,
budget halt, a gathered candidate the cluster phase never reached). Marking
neither either loses the story or re-buys its Gemini + Haiku calls every day. Two
rules keep advancing safe and must not be removed — the **retry floor** (only
decided dates strictly below the earliest unresolved date advance) and the
**same-day grace** (never advance onto the pass's own date; the source is still
publishing and `RecencyFilter` drops `published_at <= watermark`). See
`docs/PIPELINE_CHANGES_2026-07-30.md` §9. Guard: `test_watermark_advance.py`.

Two things that are easy to get wrong:
- **There is no in-process scheduler.** Cloud Run scales to zero, so one never
  fires unless you pay ~$15-25/mo for `min-instances=1` + CPU-always-allocated.
  The single-job APScheduler that used to sit in `main.py` behind
  `ENABLE_INPROCESS_SCHEDULER` was removed 2026-08-29; the daily chain runs only
  via `POST /orchestrator/daily`.
- **`ops/` must never raise.** Every module there swallows its own exceptions and
  degrades to stdlib logging. An observability layer that can crash the pipeline
  it observes turns a logging outage into a data outage.

**Stage 1 (Gemini):** Fast noise rejection. Pass threshold: confidence ≥ 0.4. Target 60–70% rejection of raw scrape volume.

**Stage 2 (Claude Haiku):** Classification, draft writing, severity scoring. Returns
JSON — see spec §4.3 for the schema, with three deltas since:

- **`pixel_art_prompt` is no longer generated by Stage 2.** It was written on
  every draft and read by nothing, because the art prompt is composed later, at
  approve time. **Art generation itself is LIVE, not dormant** — this bullet used
  to claim the approve route "hardcodes `pixel_art_url: null`", which is false.
  `apps/war-room/app/api/queue/[id]/approve/route.ts` imports
  `generateIncidentArt` from `@/lib/artGenerate`, calls it *before* the insert,
  and writes `pixel_art_url`, `image_status`, `image_prompt` and
  `image_attempts`. `lib/artGenerate.ts` POSTs to the agents backend
  `/art/generate` with `X-Ops-Token`. See `docs/ART_PIPELINE.md`.
  **It writes those columns; until 2026-08-04 the values were always null.**
  Cloud Run's IAM gate rejected every call (see the deploy section above), so
  `generateIncidentArt`'s failure paths — which all return `final_prompt: ''` —
  stored `image_prompt: null`. Two lasting consequences worth knowing:
  `image_status='pending'` does **not** mean "queued", it is the `DISABLED`
  return meaning *the backend was never reachable and nothing was attempted*;
  and a NULL `image_prompt` used to make `/rectify` a dead end, since
  "Retry as-is" fell back to a prompt that did not exist and returned
  `422 No prompt to render`. That route now runs the full `/art/generate` path
  when no prompt is stored, so the composed prompt is persisted and the next
  retry is editable.
- **The write model is Haiku**, not Sonnet (`STAGE2_WRITE_MODEL` to roll back).
  Justified by an eval over 30 real inputs; Haiku matched Sonnet on ungrounded
  specifics on the multi-source half and on format compliance.
- **`incident_date` is the EVENT date, and since 2026-08-04 something actually
  extracts one.** Stage 2's classifier returns `event_date` — read out of the
  article text ("on July 30 at about 3.57pm", "Last Friday (31 July)", "on
  Sunday (Aug 2)") and resolved against the publication date. The publication
  date is now only the FALLBACK, kept alongside as `published_date`.
  Before this the candidate's `published_at` was carried straight through, so
  every incident was filed on the day it was REPORTED. Measured on three rows
  published that morning: the python worksite story happened Jul 30 and was
  filed Aug 3; the high-beam chase Jul 31, filed Aug 3; the pliers assault
  Aug 2, filed Aug 3. The date drives the feed sort AND the slug suffix, so a
  wrong one is visible in three places. `_sanitise_event_date` rejects a date
  after publication (a model resolving "Sunday" the wrong way) or more than 5
  years before it (a misparse of some older date in the copy), falling back
  rather than filing a row under a wrong date. Guard:
  `test_stage2_guardrails.py`.
- **Summaries are written in paragraphs**, separated by a blank line (`\n\n`),
  2-4 sentences each. Added 2026-08-04; before it, every one of the 163
  published summaries was a single unbroken block and 35 ran past 900
  characters. The public page honours those breaks and falls back to sentence
  grouping (`toParagraphs`) for rows written before the change — it only ever
  inserts breaks, never edits words. Anything that puts a summary in a meta tag
  must collapse the whitespace first.
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

**Scraping:** 25 sources are wired into the live pipeline via `ingestion/sources/`
(`get_enabled_sources()`), in three tiers.

**PRIMARY — 12 MSM scrapers** reading each outlet's current feed or listing page:
*RSS-dated* — CNA, Mothership, Straits Times, MustShareNews, The Independent,
Yahoo; *HTML-scraped* — AsiaOne, Stomp, Zaobao, Shin Min, Berita Harian, Tamil
Murasu, whose listing pages carry no date, so `scrapers.resolve_published_at()`
reads it from the article (URL path, else meta tags).

**DISCOVERY — 11 adapters** added 2026-08-02, the wider net behind the spine:
- `news_sitemap.py` (9): each publisher's **own Google-News sitemap** — CNA,
  Straits Times, Yahoo, AsiaOne, Stomp, Zaobao, Berita Harian, Tamil Murasu, The
  Independent. Canonical URLs, real publication dates, and a far bigger window
  than the front-page feed: Straits Times serves **462 sitemap entries against 44
  in its RSS**. Sitemaps carry no body, so a keyword-matching entry has its
  article fetched — recency is applied *before* that fetch.
- `wp_search.py` (2): MustShareNews and The Independent answer
  `?s=<term>&feed=rss2` with a dated RSS feed of search results over their whole
  archive. Each site is searched for `yishun`, `khatib` and `chong pang`
  (`SEARCH_TERMS`) and the results merged/deduped — a `?s=yishun` feed only holds
  articles the publisher indexed on "yishun", so a subzone-only story never
  appears in it and the downstream keyword filter never sees it.

For the historical BACKFILL gap the live pass structurally cannot reach (stories
on unscraped domains, or old ones off every feed/sitemap window), `tools/search_discovery.py`
runs a web search ("yishun <year>") and emits a `seed_backfill.py` manifest of
publisher URLs. It is a run-once operator tool, **not** a daily source (topic×year
is static history). **Keyless by necessity:** every hosted search API died or went
paid-only in 2025-26 (Google Custom Search closed to new users + dies 2027-01-01;
Bing retired 2025-08-11; Brave/Tavily/Serper need a card), so it drives
DuckDuckGo's HTML endpoint and parses it with bs4 (already a dep — no new pip
requirement). Two guards matter: `_ddg_result_url` DECODES DDG's
`duckduckgo.com/l/?uddg=` redirect back to the publisher URL (storing the wrapper
would reintroduce the `news.google.com` problem the 2026-08-02 removal was about),
and `filter_links` drops redirect wrappers, forum/signal hosts and social/junk
noise. DDG rate-limits (HTTP 202) after a few quick queries — `search()` returns
`[]` and the sweep continues rather than aborting; raise `--spacing` or re-run for
a big sweep. The authoritative source_allowlist and Stage 1 both run downstream.
If DDG throttling ever blocks real work, swap the one `search()` function for a
paid backend. Guard: `test_search_discovery.py`.

**SIGNAL — 2**: Reddit (r/singapore, r/singaporeraw) and EDMW/HWZ.

Not covered, and why: **Mothership** has no news sitemap (`/sitemap.xml` just
re-serves `/feed/`) and ignores `?s=`, so its 10-entry front-page feed is the
ceiling. **Shin Min** serves no robots.txt and no sitemap at all.

> **Google News RSS was removed on 2026-08-02. Do not add it, or any other
> aggregator, back.** It had been the dominant discovery channel since the
> original ingestion build (`f27066c`, 2026-06-18). Its entries link to
> `news.google.com/rss/articles/<blob>` wrappers which do **not** HTTP-redirect —
> decoding one needs a reverse-engineered `batchexecute` RPC that Google rotates —
> so when resolution failed the code stored the **wrapper** as the article URL.
> That breaks three things at once: `Candidate.url` is contractually "canonical,
> not a wrapper" because **dedupe matches on URL**, so the wrapper matched nothing
> and the pipeline could not see it already held the story; the wrapper then
> landed in `war_room_queue.source_url` and `source_urls`, citing a redirect
> instead of the outlet that did the reporting; and `source_allowlist` cannot
> classify news.google.com, so every such row was held back as
> `unapproved_source_domain`. All three fired in production on 2026-08-01 — two
> queue rows proposing "updates" to incidents already held, each a duplicate of a
> Stomp article ingested cleanly the day before. See
> `ingestion/sources/news_sitemap.py` for the full account, and
> `source_allowlist.REDIRECT_DOMAINS` for the net now under it.

A source must supply `published_at` to be registered: a dateless candidate
bypasses the recency watermark, is re-processed by Stage 1/2 every pass, and
can't be approved until an operator sets the date by hand (QA H3).

**EDMW/HWZ and Reddit are registered as `source_type='signal'`** (EDMW Phase 3,
commit `522e09d`; Reddit July 2026). Guardrail #2 is enforced in `orchestrator.py`
via `source_allowlist.is_signal_source()` — never a plain `== 'edmw'`, because
`scrape_edmw` and `scrape_reddit` both emit the canonical `'signal'` and that vocabulary mismatch
silently breached the guardrail once already (`92d6305`).

**What counts as Yishun is `YISHUN_KEYWORDS` in `scrapers/__init__.py`, and the
scope rule is: the Yishun planning area and things inside it, nothing adjacent.**
The list is `yishun`, `khatib`, `chong pang`, `northpoint`, `khoo teck puat`.
Matching is plain case-insensitive substring, so bare `yishun` already covers
"Yishun Ring Road", "Yishun Ave 6" and friends — only names that do *not* contain
"yishun" need their own entry.

- **Sembawang is not Yishun.** It is a separate URA planning area. It sat in this
  list from the first commit (`e71d976`, 2026-06-06) until 2026-08-02 while every
  TechSpec from v1.5 carried `# NOTE: "sembawang" removed — separate town, not
  Yishun`. The spec was updated, the code never was, and nothing tested it, so the
  two disagreed for two months and it kept pulling Sembawang stories into the
  queue for the operator to reject by hand. Do not re-add it, or Woodlands,
  Admiralty, Canberra, or Sembawang Hills.
- **Khatib and Chong Pang are Yishun** — both are subzones of the planning area.
  Khatib had never been in the list at all.
- **`nee soon` is deliberately excluded** from the English list. In news copy it
  reads as the constituency (Nee Soon GRC) far more often than the place, so it
  imports exactly the political content guardrail #4 must reject; every genuine
  Yishun story in a live sample already matched on `yishun`. It is retained in the
  Malay list, where it is a place-name.

Guard: `test_yishun_geography.py`.

Scrapers **raise** `ScraperError`/`ScraperBlocked` on a source-level failure
rather than returning `[]`; the adapters translate those to
`SourceBlockedError`/`SourceUnavailableError`. An empty result therefore means
"no Yishun news", not "something broke quietly" — Stomp sat silently dead for
weeks under the old behaviour.

**`scraper_health` is written by `ingestion/health.py`**, called once per fetched
source from the orchestrator's per-source loop — keyed on the **stable source id**
(`stomp`), the same key as `pipeline_state`, so one source can never render as two
under different spellings. The previous writer (`scrapers.log_scraper_run`, inside
`scrape_all`) was orphaned by the adapter port and both are now deleted: the table
went stale while the supervisor and War Room kept reading it, which is a worse
failure than having no health table at all. See `docs/AUTONOMY.md` §5.

**Display reads `scraper_health`; alerting does not.** `ops/supervisor.py`'s
zero-streak check derives from `pipeline_run_history` (`state_store.record_run`,
written at the end of every real pass), *not* from `scraper_health` — deliberately,
even though the table now has a live writer again. An append-only table that stops
being appended to looks exactly like a healthy quiet one, and that is precisely the
failure the supervisor exists to catch, so its alerting must not be the thing that
goes quiet with it. `scraper_health` powers the War Room health views (7-day
window) and `ops/maintenance.py`'s error digest. Keep it that way: if you need a
new *alert*, base it on run history.

**A zero-item run is the normal case, and `ZERO_STREAK_WARNING` respects that.**
`items_found` counts candidates that survived the Yishun keyword filter, not
articles the source served, so one outlet publishing nothing about one town for
several days running is unremarkable — Tamil Murasu or Berita Harian can go a
month. The threshold was 3 until 2026-08-02, which made `warning` the *resting*
state of the fleet: 9 of 15 sources sat there permanently, every one reading
"0 items for 3 consecutive runs", and the health panel read as a dead fleet when
nothing had failed. It is now **30** — a month of genuine silence. This is a
display signal only; real failures are `status='error'`, and outage alerting
lives in `ops/supervisor.py` off `pipeline_run_history`.

**The supervisor's own zero-streak *alert* threshold is that same 30, imported
from `ingestion/health.py` rather than a second copy of the number (2026-08-29).**
`report.per_source[].fetched` — what the alert-side streak is measured on — is
POST-keyword-filter for every source, primary tier included, for the identical
reason: every `scrapers.scrape_*` module filters before returning anything. An
earlier fix gave only discovery-tier sources (`_sitemap`/`_search` ids) a longer
leash and left primary sources at 5, which fired as false "anomalous" primaries
on ordinary Yishun silence — fixing the assumption for one tier without noticing
it was wrong for both. There is one tier and one threshold now. See
`docs/AUTONOMY.md` §3.

**The supervisor's email dedup compares a SIGNATURE of what's broken, not a
fixed key (2026-08-29).** The dedup key used to embed the sorted broken-source
list, so any churn in *which* sources were anomalous changed the key and the
once-a-day throttle never engaged — the same standing fleet problem mailed
twice in one day with "slightly different" source lists. It now reads back the
signature from the last actual `operator_notified` event and only mails again
when the current signature differs (a source newly broken, or one recovering);
`is_serious()`'s reasons are still computed and logged every pass regardless —
only the email is gated. See `docs/AUTONOMY.md` §3.

---

## Database

Supabase, `public` schema. RLS enabled on all tables — public reads only, all writes via `SUPABASE_SECRET_KEY` from agents backend only.

Key tables: `incidents`, `sources`, `war_room_queue`, `utm_events`,
`training_signals`. (`chaos_index_snapshots` exists but is **never written** —
see the Chaos Index section.)

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
checked against this table. **Three rules, deliberately different in severity** —
`classify()` returns `redirect` | `signal` | `approved` | `unapproved`:

| Verdict | Action | Why |
|---|---|---|
| `redirect` | **Removed** unconditionally | A citation must point at the publisher, not a wrapper |
| `signal` | **Removed** unconditionally | Guardrail #2 |
| `unapproved` | **Kept and flagged** in `raw_content._source_allowlist` | Stripping it could take an incident's last source and break guardrail #1 |

`redirect` is checked **first and does not consult the sources table**, so the
rule cannot be defeated by someone adding `news.google.com` to `sources`.
`REDIRECT_DOMAINS` covers news.google.com, google.com, feedproxy.google.com and
the common shorteners (t.co, bit.ly, apple.news, …). `check_source_urls()`
returns a `dropped_redirect` list alongside `dropped_signal`, and
`consolidation/queue_row.py` also substitutes a real publisher URL when the
candidate's own `source_url` is a wrapper — that field is what the War Room
renders and what `dedup.is_duplicate` matches on, and it used to be copied across
with no check at all.

Dropping can empty `kept`. That is intentional and not special-cased: a candidate
whose only citation was a wrapper has no verifiable source, which is precisely
what guardrail #1 exists to catch. It lands in the queue as unverified, like a
signal-only candidate, and waits for an operator to attach a real one.

Matching is suffix-aware throughout, so `cnalifestyle.channelnewsasia.com`
inherits CNA's approval — and `rss.news.google.com` inherits the redirect block.

**Migrations are hand-applied in the Supabase SQL Editor (no runner).** Apply in
order; the live DB depends on `006_phase1_apply_now.sql` + `007` + `009` having all
run. `006_SUPERSEDED_DO_NOT_RUN_ingestion_learning_loop_schema.sql` is exactly what
its name says (renamed 2026-07 so it can no longer be mistaken for a live step).
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

**013 (RLS fix + reddit seed cleanup)** — ⚠️ security migration, apply promptly.
Migration 003 had created `USING (true) WITH CHECK (true)` policies with no `TO`
clause on `pattern_alerts` and `people_profiles`, which despite their names gave
the **anon/publishable key full read AND write** on both tables. 013 drops them
(RLS stays enabled with no policy → service-role only, like every other private
table). It also removes the two reddit URLs that 005 seeded into
`incidents.source_urls` (guardrail #2 breach once 012 reclassified reddit as
signal) and decrements those rows' `corroboration_count`. `tools/rls_audit.py`
now covers both tables.

**016 (signal URLs in `source_urls`)** — guardrail #2 cleanup, same shape as
013's but for rows the LIVE pipeline produced rather than the 005 seeds. An
audit on 2026-08-03 found four published incidents quoting a reddit thread as a
citation, each counting it in `corroboration_count` (so the public "Corroborated
by N sources" line was inflated by one). The ingestion path itself is already
correct — `classify()` resolves reddit to `signal` today, verified — so these
are legacy rows from before the July-2026 reclassification, not a live leak.
§1 cleans three of them. The fourth, `yishun-remote-gambling-bust-17-arrested-jul-2026`,
has a reddit thread as its **only** citation: removing it would leave zero
sources and violate guardrail #1's CHECK, so §1 skips it by design and §2
leaves the operator two commented-out choices (attach real reporting, or
unpublish). Apply the data change with
`packages/agents/tools/repair_display_data.py --apply`, which performs §1's
semantics via REST — this project has no SQL runner.

**014 + 015 (image status)** are load-bearing for the live art pipeline.
`014_image_status.sql` adds `incidents.image_status`, `image_prompt` and
`image_attempts`; `015_image_status_check.sql` adds the CHECK constraining the
status vocabulary. Without 014 the War Room rectify queue errors out — it selects
those columns directly. Art generation runs on the operator approve path, so
these are not optional.

**018 (undo an applied update)** — adds `'update_reverted'` to
`war_room_queue.status` and `'auto_update'` + `'update_reverted'` to
`training_signals.action`. A confirmed update (merge) mutates a live incident;
`confirm-update` now snapshots the pre-merge state into
`raw_content._undo_snapshot` and the War Room queue page shows a "Recently
merged updates" panel with an Undo (`/api/queue/[id]/revert-update`), which
restores the snapshot. `'auto_update'` is reserved for the autonomous auto-merge
(PR #2, still dark). ⚠️ Same failure mode as 009/011: without the action values
the revert/auto-merge training-signal insert is silently rejected — the mutation
still happens, only the learning signal is lost. Merge math lives once in
`apps/war-room/lib/utils.ts` (`applyUpdate`/`revertUpdate`); guard
`apps/war-room/lib/utils.updateMerge.test.ts`.

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
5. A `suicide` / `self-harm` incident never gets a graphic image. Since
   2026-08-09 (operator direction) the DEFAULT is not "no image" but a fixed,
   non-graphic **police/SCDF-response scene** that never depicts the body, the
   act or the method. `art/sensitive_scene.py::incident_kind` picks one of three
   so it matches the outcome: a shut blue privacy-tent cordon (fatal, default),
   an SCDF ambulance response (a death indoors — no ground tent), or an SCDF
   air-cushion rescue (nobody died — no tent). `SENSITIVE_INCIDENT_ART=suppress`
   restores the original no-image behaviour. Detection stays deliberately narrow — severity, death count and
   confidence are not consulted, and all other categories generate normally.

> **Guardrail #5 splits into a DETECTOR and a POLICY, and the code makes that
> split explicit.** `art/suppression.py::suppress_image()` is only the detector:
> it answers "is this a suicide / self-harm story". `tags` is written by the
> Haiku classifier, so a tag-only gate would make the one check that must not
> fail depend on a model output — and the classifier does sometimes omit a
> `suicide` tag on a suicide story. So the detector ORs the tag check with a
> deterministic phrase match over the incident's own title and summary, and fails
> **closed** (an unreadable input is treated as sensitive).
>
> The POLICY lives in `art/generate_image.py` + `art/sensitive_scene.py` and is
> switched by `SENSITIVE_INCIDENT_ART` (default `respectful`, rollback
> `suppress`; any other value resolves to `suppress`). Respectful mode renders a
> **fully deterministic** scene — no Haiku, so the scraped summary never becomes
> picture content; only a place-TYPE (HDB block / void deck / carpark / corridor)
> is inferred, and water settings fall through to a neutral default rather than
> depict water. The assembled scene is screened by `scene_is_clean()` against a
> forbidden-word set, and **any** failure to produce it safely — an un-clean
> scene, an unreadable incident, or a safety refusal from the image model — falls
> back to suppression (no image). We never mutate a sensitive scene to get one
> past the filter, and the operator rectify path re-renders the tableau rather
> than honouring a hand-typed prompt. See `docs/EDGE_CASES_AND_HARDENING.md` §1.2,
> `test_image_suppression.py` and `test_sensitive_art.py`. Over-suppression costs
> a placeholder; under-suppression does not.

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
>   marker, an operator alert (Telegram since 2026-08-27, email before that) and a
>   `warning` `agent_events` row — because a silently-zeroed row was
>   indistinguishable from any other low-confidence row.
>   **Since 2026-08-02 the guardrail is evaluated BEFORE field validation.** It
>   used to sit below the classification coercion, and
>   `result["classification"].lower()` threw `AttributeError` on
>   `"classification": null` — which is what the model returns on a political
>   story, because it is being told to reject rather than categorise. The
>   candidate died on an exception, so confidence was never forced to 0, the
>   marker was never prepended, and neither the operator alert nor the
>   `agent_events` row ever fired. The guardrail was unreachable for a subset
>   of exactly the content it exists to catch. **Never move the political
>   check back below field validation.**
>   **Also since 2026-08-02: `write_stage2` SKIPS the writer model entirely when
>   `political` is true.** It used to call `_write_draft` unconditionally, asking
>   the model to write tabloid copy about an "incident" that by definition isn't
>   one. Haiku refuses with prose rather than JSON, `_parse_json` raised, and
>   nothing caught it — in the live pass that surfaced as a `cluster write error`,
>   which cannot tell a deterministic refusal from a transient fault, so it held
>   the whole cluster `unresolved` and retried it. A political candidate never
>   stops refusing, so it and every innocent sibling in its cluster jammed behind
>   the watermark retry floor, re-buying the same failing call daily. The stub
>   draft is synthesised deterministically; confidence is already 0 and the reject
>   marker still lands, so nothing is weakened.
> - **#3** still has no programmatic check — operator-gate only.
>
> Regression guards: `test_stage2_guardrails.py`, `test_political_alert.py`,
> `test_source_allowlist.py`, `test_yishun_geography.py`. Strengthen these freely;
> never weaken them.

---

## Frontend Theme

> **The one-page HUD was split into two routes in the 2026-08 restructure
> (merged to `main`; `docs/FRONTEND_SPEC.md` §3–4 is the canonical authority,
> `docs/WEB_RESTRUCTURE_2026-08-07.md` is the historical plan).** `app/(hud)/`
> groups **Feed (`/`)** and **Map (`/map`)** behind a shared Chaos panel. The
> year and class filter are **`?year=` / `?class=` URL params** (`lib/params.ts`),
> so they persist across `/↔/map`; the **Incident Breakdown rows ARE the class
> filter** (no content chip bar).
>
> - **Map** — **emoji-only** `maplibregl.Marker` pins (❤️🤡💀, no circle badge). A
>   symbol layer can't render emoji, and centring an emoji in a circle is
>   unreliable, so the emoji is the pin. Hover/tap → preview popup with an art
>   thumbnail + summary teaser (`lib/teaser.ts`, escaped).
> - **Feed (`/`)** — **banner news-article cards** (`NewsCard`): image on top, a
>   23px headline, a teaser; **READ MORE expands in place** (several open) to the
>   full write-up + casualties + dated sources + story timeline + a `Full page ↗`
>   link. Infinite scroll (`react-window` removed). The class emoji is in the meta
>   row, not on the image.
> - **History (`/timeline`)** — compact `IncidentCard` rows keep the thumbnail
>   layout (info-row emoji, no image box).
> - **Nav** — `FEED | MAP | HISTORY | ABOUT` (dim `|` separators). `HISTORY` is a
>   label only; the route stays `/timeline`.
> - **Mobile** — the sidebar becomes a **bottom sheet** (first responsive layer);
>   nav/chips/type are responsive.
> - **Images** — served via `next/image`; `next.config.js` sets
>   `images.contentDispositionType: 'inline'` so "open image in new tab" previews
>   instead of downloading.

Dark pixel art retro tabloid. Two fonts only: `Press Start 2P` (headers, scores, logo) and `Courier Prime` (all body text). Three sizes max: 24–28px, 11–12px, 8–10px. Two weights: 400 and 700.

CSS tokens are defined in spec §6.1. Key colours: bg `#0D0D0D`, accent red `#E74C3C`, accent yellow `#F1C40F`, dagger purple `#8E44AD`.

Map: MapLibre GL JS with OpenFreeMap "Liberty" style (`https://tiles.openfreemap.org/styles/liberty`). Keyless — no Mapbox token, no Stadia/CartoDB. `IncidentMap.tsx` reads `NEXT_PUBLIC_MAPLIBRE_STYLE` with a hardcoded fallback to the same Liberty URL (`||`, so an empty-string env var also falls back), so the map can never be a single point of failure if the var is unset. A set-but-wrong env var overrides the fallback. Because `NEXT_PUBLIC_*` vars are baked at build time, changing it requires a fresh deploy, not just a restart.

**Liberty ships light and is recoloured dark-green at runtime** (`tintMap` in
`IncidentMap.tsx`, called on `load`). The point is pin legibility: the coral /
teal / yellow classification pins have almost no contrast on Liberty's stock
white-and-pastel streets. The recolour **walks the live style and assigns by
layer id** rather than listing layers — Liberty has 111 of them, ~50 being
near-white `tunnel_*` / `bridge_*` / `aeroway_*` variants that a hand-written
table drifts out of sync with. Three things worth knowing before touching it:
- **`setPaintProperty` on a missing layer does not throw, it fires an `error`
  event.** A `try`/`catch` around it catches nothing and the map's own error
  handler then reports a style failure every frame. Walking the live layer
  list is what makes that impossible.
- **Pattern fills ignore `fill-color`.** `landcover_wetland` and
  `road_area_pattern` carry their own light sprite pixels, so they stay bright
  holes in the dark map unless faded via `fill-opacity`.
- **Liberty's POI/shield icon layers are hidden outright** (`HIDDEN_LAYER_RE`).
  At Yishun zoom there are dozens, all more saturated than the basemap, and
  they competed directly with the incident pins — which are the only thing on
  this map anyone came to look at.

**Lightning (⚡) = corroboration, not a separate hype field.** As of the June-2026 feed pass, the lightning meter is derived live from `corroboration_count`: `bolts = max(0, corroboration_count − 1)` (2 sources → ⚡, 3 → ⚡⚡, …). It grows as sources merge into one incident. The legacy `hype_meter` column is no longer read by the frontend. The **DEVELOPING** badge/banner was removed (it confused readers); `is_developing` drives the report-count line only — the feed is sorted newest-first (`incident_date DESC`, `id` tiebreaker), not by `is_developing`. The story timeline collapses same-date entries to a single node, and "time to verdict" is computed from the last verdict/sentencing/appeal entry in `source_timeline` (never `incident_date`). See `docs/FRONTEND_SPEC.md` and `lib/utils.ts` (`hypeFromSources`, `lastVerdictEntry`, `collapseTimelineByDate`).

**The source count is counted, not trusted (2026-08-04).** "Corroborated by N
sources", the lightning meter, the `Sources (N)` heading and the feed card's
`N sources` all derive from the SAME `source_urls` array the page lists
underneath, so the number can never disagree with the links. `corroboration_count`
remains the DB column (War Room, auto-publish, training signals) but the public
site no longer displays it. The feed queries therefore select `source_urls` —
if you add another surface that renders `IncidentCard`, it must select it too or
the card silently falls back to `corroboration_count`.

**Every source link shows its article's publication date.** Resolution order:
the `source_timeline` entry for that URL, then a date the publisher stamped into
the URL path (`dateFromUrl` — malaymail `/2018/07/13/`, zaobao `storyYYYYMMDD`).
Neither → the link renders **"Undated"**, never the incident date: `incident_date`
is the EVENT date and a follow-up filed two years later shares neither it nor
`published_at`. Two supporting fixes: `consolidation/queue_row.py` now synthesises
a timeline entry per kept source URL (`build_cluster_stage2_input` only emitted
one for a multi-article cluster, so single-source stories published undated), and
`tools/backfill_source_dates.py` resolves the historical rows by fetching each
article.

**Map pins: the address may live in the headline.** `classifiers/geocoding.py`
reads the block and street from the `block_number`/`area_name` columns first and
mines the title, then the summary, as a fallback. Before that fallback (added
2026-08-04) an incident whose address appeared only in its headline built *no
geocode query at all* — 68 of the 71 unpinned published incidents were in that
state, including "NSF dies after being pinned down at Block 279 Yishun Street
22" with `block_number = NULL`. **The POI whitelist is still never scanned over
the summary**, and that asymmetry is deliberate: an address is a specific
phrase, whereas every dagger story mentions "taken to Khoo Teck Puat Hospital"
and mining POIs from prose would pin them all at the hospital. Rows that name no
location anywhere still get NO pin — never the Yishun centroid.

**Since 2026-08-13 the SLUG is mined too, and it is often the only place the
location survives.** A headline is written around the event while the slug keeps
the place: `khoo-teck-puat-hospital-opens-yishun-2010` has the title "Yishun gets
its own hospital after north residents spent decades travelling", which names
nothing geocodable. Callers pass `f"{title} {deslug(slug)}"`, so the existing
POI, block and street miners all see it — mining the slug is safe for the same
reason the title is (a compressed headline naming the story's own subject), and
the summary remains POI-exempt. Two other pin-losers were closed at the same
time, both of which failed **silently**:
- **Eight POI whitelist entries were dead queries** (`YISHUN INTEGRATED
  TRANSPORT HUB`, `YISHUN PARK HAWKER CENTRE`, `YISHUN STADIUM`, `YISHUN PUBLIC
  LIBRARY`, `CHONG PANG MARKET AND FOOD CENTRE`, `JUNCTION NINE`, `NORTH VIEW
  PRIMARY SCHOOL`, `ORCHID COUNTRY CLUB`). OneMap returned nothing for any of
  them, so an incident naming one of those places fell through to "no pin" with
  no error anywhere. **Verify a new alias against OneMap before adding it** — a
  dead alias is invisible.
- **A dropped OneMap request was indistinguishable from "no such place".**
  `_onemap_lookup` now retries transport failures (3 attempts, backoff) and
  never retries an empty result, so a genuine miss still falls straight through
  to the next query in the priority order.

**A WRONG pin is worse than a missing one, and OneMap will hand you one.** Its
search is fuzzy and always ranks *something* first, so an unindexed place
resolves to an unrelated neighbour that still passes the Yishun bounds check —
`YISHUN DAM` returned "Nam Hong Siang Theon", a temple on Yishun Ring Road
3.4 km away, and that pin shipped on two published incidents. Two guards now
sit in `_onemap_lookup` (both mirrored in the War Room's `lib/geocode.ts`):
- **`_result_matches_query`** requires the accepted hit to share one
  distinctive token (stopwords like `YISHUN`/`BLK` excluded) with the query,
  so a fuzzy near-miss is rejected as if there were no result.
- **`_VERIFIED_COORDS`** is a tiny hardcoded table for places OneMap has no
  record of at all, checked *before* the API call. Only `YISHUN DAM` so far
  (OpenStreetMap `natural=dam`). Every entry needs a named source in the
  comment — a coordinate nobody can re-derive is worse than no pin.

Guards: `test_geocode_address_mining.py` (34 checks, incl. slug mining, the
dead-alias assertion, and the fuzzy-match / Yishun-Dam guards).

**Co-located pins are fanned out at RENDER time, not in the data.** Several
incidents at one block resolve to the same coordinate, so their markers stacked
and only the last was clickable. `spreadOverlappingPins` (`apps/web/lib/utils.ts`,
called in `IncidentMap.renderMarkers`, which both the SSR and year-change paths
flow through) spreads a stack around a ~20 m circle — inside the block
footprint. The offset derives from position in the stack, **not** randomness: a
random jitter moves every pin on each re-render, so a pin the user is reaching
for slides out from under the cursor. The stored coordinate stays the true
address. Guard: four cases in `apps/web/lib/utils.test.ts`.

**A `same_location` related link names the location.** `sharedLocationLabel`
intersects the two incidents' own `area_name`/`block_number` — block-level when
both agree, else the street. All 137 confirmed `same_location` links share an
`area_name`. A shared area of just "Yishun" returns null and the link renders
"Same location" alone, because an entire town is not a location worth printing.
Nothing here is hardcoded; do not hardcode it.

Share cards: rendered via OG meta tags — no separate image generation. The
pixel art image doubles as the OG image, which is why generated images must be
exactly 1200×630 (the dimensions are hardcoded in
`apps/web/app/incidents/[slug]/page.tsx`).

**Art pipeline:** see `docs/ART_PIPELINE.md`. The SDXL/Modal/LoRA pipeline was
removed in July 2026 and replaced with `gemini-3.1-flash-lite-image`. TechSpec
§9 is historical — do not build from it.

---

## Chaos Index

**Computed on read, not on publish. Nothing writes `chaos_index_snapshots`.**
This section used to say the score was "computed on every new publish, stored in
`chaos_index_snapshots`" — it never was. The table exists (migration 001) and the
only reference to it anywhere in the codebase is a *read* in
`orchestrator/herald_agent.py`, which needs ≥ 2 rows to fire, so that milestone
can never trigger. The live score is calculated on every request by
`computeChaosScore()` in `apps/web/lib/utils.ts`, used by `/api/chaos` and the SSR
homepage. If you want snapshots, you have to build the writer.

**Per-incident points** (these are what Stage 2 stores as `chaos_contribution`):
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
OPS_TOKEN                  # shared secret: War Room -> agents /art/* endpoints
AGENTS_API_URL             # War Room -> agents base URL
REVALIDATE_SECRET          # War Room -> web /api/revalidate (must match web's)
NEXT_PUBLIC_SITE_URL
WAR_ROOM_URL
```

`MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` were removed 2026-08-02 — the SDXL/Modal
pipeline is gone (`docs/ART_PIPELINE.md` §7.4). Nothing in the codebase reads them.

`SUPABASE_SECRET_KEY` and API keys go in Google Cloud Run env vars — never in `.env` files committed to the repo.

---

## What Is Not Being Built (Phase 1)

No user accounts, comments, votes, TikTok pipeline, distribution orchestrator, monetisation, mobile app, admin user roles, or public API.
