# yishun-again
Yishun - You'll never find a more wretched hive of scum and villainy.

A satirical, semi-autonomous incident archive for Yishun, Singapore. An agent
pipeline scrapes Singapore news sources, filters and drafts incident write-ups,
and either auto-publishes them or queues them for operator review in a private
CMS.

**Core constraint:** every published incident links to a verifiable source, and
that link points at the **publisher** — never an aggregator or a redirect
wrapper. No private individuals unless named in MSM. No political content, ever.

Reddit is a `signal`, not a quotable source: it is user-generated discussion, not
journalism, and a thread reviving an old case carries a recent post date. MSM is
the sole authority for both the citation and the event date.

Full spec: `docs/YishunAgain_TechSpec_v1_9.md`. Read `docs/AUTONOMY.md` before
changing anything in `packages/agents/ops/`.

---

## Layout

```
apps/web/          Next.js — public site (Vercel)
apps/war-room/     Next.js — private operator CMS (Cloudflare Access)
packages/agents/   FastAPI agent pipeline (Cloud Run, asia-southeast1)
packages/db/       Supabase migrations
infra/             Cloud Build config, Cloudflare R2 config
docs/              Spec, autonomy runbook, pipeline change records
```

## Stack

| Layer | Tool | Version |
|---|---|---|
| Frontend | Next.js App Router | 16.2.x (React 19.2.x) |
| Map | MapLibre GL JS | 3.x, OpenFreeMap "Liberty" style (keyless) |
| CSS | Tailwind CSS | 3.x |
| Database | Supabase (Postgres + REST), RLS on every table | — |
| Image storage | Cloudflare R2 | — |
| Backend | FastAPI / Python | 0.115.x / 3.11 |
| Stage 1 filter | Gemini API | `gemini-3.1-flash-lite` |
| Stage 2 classify + write | Anthropic API | `claude-haiku-4-5-20251001` |
| Image gen | Gemini image API | `gemini-3.1-flash-lite-image` |
| Scheduling | Cloud Scheduler → `POST /orchestrator/daily` | 14:58 SGT daily |

`langgraph` is pinned in `requirements.txt` but nothing imports it — the
orchestration is hand-rolled in `ops/daily.py` and `ingestion/orchestrator.py`.

APScheduler is a dependency and `main.py` registers the daily chain with it, but
it is **off in production** (`ENABLE_INPROCESS_SCHEDULER=false`): Cloud Run
scales to zero, so an in-process scheduler never fires. Cloud Scheduler is the
only cadence in production.

## Pipeline

```
Scrape → Stage 1 (Gemini) → cluster by story (one Haiku call) → Stage 2 writer (Haiku)
       → deterministic groundedness + casualty cross-checks → consolidation → war_room_queue
       → all gates clear AND confidence >= 0.95 ? auto-publish : operator review
```

Publishing renders the pixel art **before** the insert, so `pixel_art_url` and
`image_status` are in the row from the start: `ops/auto_publish.py` calls
`art.generate_image` in-process, and the War Room approve route POSTs to the
agents backend `/art/generate`. `art/suppression.py` short-circuits both for
`suicide` / `self-harm` incidents (guardrail #5).

## Sources

`ingestion/sources/get_enabled_sources()` returns **25** adapters:

- **12 MSM scrapers** — CNA, Mothership, Straits Times, MustShareNews, The
  Independent and Yahoo are RSS-dated; AsiaOne, Stomp, Zaobao, Shin Min, Berita
  Harian and Tamil Murasu have dateless listing pages, so
  `scrapers.resolve_published_at()` reads the date from the article.
- **9 news-sitemap adapters** (`news_sitemap.py`) — each publisher's own
  Google-News sitemap. A much wider window than the front-page feed (Straits
  Times: 462 sitemap entries against 44 in RSS). Mothership publishes no news
  sitemap; Shin Min serves neither robots.txt nor a sitemap.
- **2 WordPress search adapters** (`wp_search.py`) — `?s=<term>&feed=rss2` over
  MustShareNews and The Independent, which searches their whole archive. Each is
  queried for `yishun`, `khatib` and `chong pang` (a `?s=yishun` feed only
  contains articles the publisher indexed on "yishun", so a subzone-only story is
  never in it), results merged and deduped by link. Mothership ignores `?s=`, so
  it is not covered.
- **2 signal sources** — Reddit (r/singapore, r/singaporeraw) and EDMW/HWZ.

A source must supply `published_at` to be registered: a dateless candidate
bypasses the recency watermark and gets re-filtered on every pass.

Google News RSS was **removed on 2026-08-02**. Its
`news.google.com/rss/articles/<blob>` wrappers do not HTTP-redirect, and when
resolution failed the wrapper itself was stored as the candidate URL — which
broke dedupe (it matches on URL), put a redirector where a citation belongs in
`war_room_queue.source_url` and `incidents.source_urls`, and tripped
`unapproved_source_domain`. Two live rows on 2026-08-01 showed all three. The
replacements emit canonical publisher URLs, and
`classifiers/source_allowlist.REDIRECT_DOMAINS` is the net underneath: `classify()`
checks for a redirector **first**, without consulting the `sources` table, so the
rule cannot be defeated by adding the host to `sources`. Do not add an
aggregator back.

Keyword scope is the Yishun planning area only:
`["yishun", "khatib", "chong pang", "northpoint", "khoo teck puat"]`.
"sembawang" is a separate URA planning area and is deliberately absent. "nee
soon" is excluded from the English list because it reads as the constituency and
imports political content banned by guardrail #4; it is kept in the Malay list.
Guard: `test_yishun_geography.py`.

## Setup

Frontend (per app — `apps/web` on port 3000, `apps/war-room` on 3001):

```bash
npm install
npm run dev
npm run build
npm run lint
npm audit          # before deploy
```

Agents backend:

```bash
cd packages/agents
pip install -r requirements.txt
uvicorn main:app --reload --port 8080
```

Copy `.env.example` to `.env` and fill it in; it documents every variable and why
its default is what it is. Secrets live in Cloud Run env vars / Secret Manager,
never in a committed `.env`.

**Tests are standalone scripts, not pytest modules.** Each `test_*.py` in
`packages/agents/` runs top-level assertions and ends in
`raise SystemExit(1 if failed else 0)`, so `pytest` fails at collection. Run them
directly; they are offline (no network, no API keys, no DB):

```bash
./.venv/Scripts/python.exe test_stage2_guardrails.py           # one file
for f in test_*.py; do ./.venv/Scripts/python.exe "$f" || echo "FAIL $f"; done
```

**Migrations are hand-applied in the Supabase SQL Editor — there is no runner**
(tracked as QA M15). `packages/db/migrations/` runs through **015**; apply in
order. `006_SUPERSEDED_DO_NOT_RUN_*.sql` is exactly what its name says.

## Deploy

```bash
# Frontend — Vercel (deploys on git push)
vercel deploy --prod

# Agents backend — Cloud Run
gcloud run deploy yishun-agents --source packages/agents \
  --region asia-southeast1 --platform managed --allow-unauthenticated \
  --timeout=3600 --memory=1Gi --min-instances=0 --max-instances=2
```

⚠️ **`--allow-unauthenticated` is deliberate — do not "harden" it back.** Auth is
`OPS_TOKEN` (`X-Ops-Token` on every route but `/health`). `--no-allow-unauthenticated`
REWRITES the service IAM policy and drops `allUsers`, which blocks the War Room —
it runs on Vercel, has no GCP identity, and sends only `X-Ops-Token` — at the
edge with `403 … Empty Authorization header value`. That is what kept the art
pipeline at one image for its entire life. See CLAUDE.md § Deployment.

`--timeout=3600` is required: a daily pass runs 5–20 min, well past the 300 s
default. `--min-instances=0` is the cost control (`docs/AUTONOMY.md` §6).

## Legal guardrails (hardcoded — never remove)

1. `source_urls` must contain ≥ 1 URL — enforced by a DB constraint,
   `CHECK (cardinality(source_urls) >= 1)` (migration 010).
2. Sources with `type='signal'` (EDMW/HWZ and Reddit) are never included in
   `source_urls` — enforced via `source_allowlist.is_signal_source()`, never a
   bare `== 'edmw'`.
3. No personal information beyond what appears in public source URLs — operator
   gate only, no programmatic check.
4. Political content → `confidence = 0`, `"[POLITICAL CONTENT DETECTED — REJECT]"`
   marker, operator email and a `warning` `agent_events` row. Evaluated in
   `filters/stage2_writer.py::_classify` **before** field validation, because a
   `"classification": null` (what the model returns on political stories) used to
   throw before the guardrail was reached.
5. Image generation is suppressed for `suicide` / `self-harm` incidents.
   `art/suppression.py` ORs the classifier tag with a deterministic phrase match
   over the title and summary, and fails **closed** — the one check that must not
   fail cannot depend solely on a model output.

Regression guards: `test_stage2_guardrails.py`, `test_political_alert.py`,
`test_source_allowlist.py`, `test_image_suppression.py`. Strengthen these freely;
never weaken them.

## Chaos Index

Computed **on read** by `computeChaosScore()` in `apps/web/lib/utils.ts` — the
only place it is calculated, used by `/api/chaos` and the SSR homepage:

```
raw   = Σ (severity × weight) for the year, floored at 0    dagger ×3.0, clown ×1.5, heart ×−1.0
score = round(100 × (1 − e^(−raw / 300)))
```

Rebalanced July 2026 from a linear `min(100, raw / 300 × 100)`, which pegged a
year at 100 permanently after 20 severity-5 daggers. The curve now approaches 100
asymptotically without reaching it. Descriptors: Quiet / Simmering / Elevated /
Critical / Apocalyptic at 0 / 20 / 40 / 60 / 80.

⚠️ The index tracks **archive coverage** as much as reality — thin historical
years read Quiet because few incidents are catalogued, not because Yishun was
calm. Comparing years is not apples-to-apples.

The `chaos_index_snapshots` table exists but nothing writes it; the only
reference is a read in `orchestrator/herald_agent.py`.

## Not in Phase 1

No user accounts, comments, votes, TikTok pipeline, distribution orchestrator,
monetisation, mobile app, admin roles, or public API.
