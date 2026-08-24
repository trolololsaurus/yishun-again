# Ingestion Architecture — Design Document

**Status:** APPROVED (owner sign-off) · **BUILT AND LIVE** · Q1–Q4 resolved (§11) · **Targets:** TechSpec v1.9 · **Scope:** Option B (trigger-agnostic)
**Companion:** `docs/LEARNING_LOOP.md` (how the Futurist agent improves from War Room signal).
**Owner-approved.** The Learning Loop is Phase-1 (contextual) only.
**Reconciled against the working tree 2026-08-02** — the design is implemented in
`packages/agents/ingestion/`; where the built code diverged from the design, this
document now describes the code, and says why it diverged.

---

## 0. Purpose & scope

This document specifies the **forward-looking ingestion layer** for Yishun Again — the
subsystem that autonomously discovers *new* Yishun incidents on an ongoing basis and queues
them for operator review.

It does **not** cover historical backfill (done by hand + `backfill_agent.py`, now complete),
nor the deployment/trigger infrastructure (see §1, Deployment Prerequisite — flagged but
deliberately out of scope here).

### ⚠️ This layer SUPERSEDED the existing live pipeline (Path A — DONE)

Investigation found **three** forward-pipeline orchestrators in the codebase, in different states:

| Orchestrator | Consolidation routing? | Status (before this work) |
|---|---|---|
| `orchestrator/orchestrator.py::run_graph()` (LangGraph 6-node) | ❌ none — every item becomes a fresh `pending` card; no update/skip, no incident_links | **LIVE** (wired into main.py scheduler + `/pipeline/run`) |
| `pipeline.py::run_pipeline()` | ✅ full (update/skip/new + incident_links) | **DEAD** (nothing imports or calls it) |
| `ingestion/orchestrator.py::run_ingestion_pass()` | ✅ via shared `consolidation/` module | **THIS DESIGN** (to be built) |

Consequence worth stating plainly, because it is why the cutover happened: under
`run_graph()` **the live pipeline had NO consolidation routing** — every candidate became a new
`pending` card, so duplicates did not reinforce, later reports did not enrich timelines, and
recurring items were not linked into phenomena. The only code that ever did this correctly was
the dead `pipeline.py`.

**Decision (Path A): build `ingestion/` fresh; retire BOTH old orchestrators. Executed.**
- `run_ingestion_pass()` **replaced** `run_graph()` as the live forward pipeline. `main.py`
  now calls it from `/pipeline/run`, and `ops/daily.py`'s ingestion step calls it on the daily
  chain. `orchestrator/orchestrator.py` is **deleted** — `orchestrator/` holds only
  `herald_agent.py`, which `run_ingestion_pass()` and `backfill_agent.py` both still use.
- `pipeline.py` was **mined then deleted**: its consolidation-routing + `write_incident_links`
  on-update logic + EDMW-aware row fields are the reference implementation for the shared
  `consolidation/` module (§5.4). The file no longer exists.
- **End state: ONE forward pipeline.** No LangGraph graph, no orphaned `pipeline.py`, no third
  path. (Owner directive: "don't keep dead code and useless agents.")
- `langgraph` has been removed from `packages/agents/requirements.txt` (2026-08-24 — never
  imported). Orchestration is hand-rolled in `ops/daily.py` (cadence) and
  `ingestion/orchestrator.py` (the pass).

Rationale for fresh-build over refactoring the LangGraph graph in place: the graph is married to
the broken in-process-APScheduler trigger (§1 / TechSpec §11.2) and to the LangGraph framework
dependency; the design deliberately uses plain functions behind one trigger-agnostic seam for
drift-resistance. Refactoring in place would preserve both problems. A fresh build retires them.

### Design goals (in priority order)
1. **Correctness over coverage.** A small set of genuinely-new, well-sourced candidates beats a
   large noisy set. Trust nothing; verify dates and dedupe before queuing.
2. **No silent failure.** A blocked or empty run must be *visibly* degraded, never a quiet
   "no news today" that hides a broken source.
3. **Drift resistance.** A narrow, verifiable contract (`run_ingestion_pass()`) that an
   autonomous agent — or a human — can reason about without understanding deployment infra.
4. **Source pluggability.** New sources (paid API, additional scrapers) drop in behind one
   interface without touching orchestration logic.
5. **Reuse existing, trusted contracts.** Terminate at `war_room_queue` (§3.5), honour the
   corroboration rule (§4.4) and the Stage 1 quota guard. Build on what is verified to
   exist, not on stale spec sections. (That guard was `scrapers/groq_budget.py` when this was
   written; Stage 1 moved to Gemini and it is now `filters/stage1_quota.py` — requests-per-day,
   not tokens — wrapped for cross-invocation persistence by `ingestion/budget.py`, see §10.)

---

## 1. ✅ DEPLOYMENT PREREQUISITE (was flagged debt — RESOLVED July 2026)

**The trigger mechanism this design refused to specify has since been built, exactly along the
recommended line. The seam did its job: nothing in `ingestion/` changed to accommodate it.**

The original problem: TechSpec §11.2 deploys Cloud Run with `--min-instances 0`, while TechSpec
§4.1 / §4.6 relied on **in-process APScheduler timers** "embedded in FastAPI." These are mutually
incompatible — `min-instances 0` scales the container to zero between HTTP requests, which
**terminates every in-process timer.** Under the documented deployment, no scheduled scrape could
ever fire autonomously.

**This design deliberately did NOT specify the fix.** Instead it exposes a single
trigger-agnostic entrypoint, `run_ingestion_pass()` (§4), that any caller can invoke. The
recommended fix was:

> **Cloud Scheduler → HTTP push → an endpoint on the Cloud Run service.**
> Cloud Scheduler is a managed cron; it issues an authenticated HTTP request on a schedule;
> Cloud Run wakes, serves one ingestion pass, and scales back to zero. This is the standard
> serverless-cron pattern, costs negligibly, and resolves the `min-instances 0` contradiction.

**What was built:** one Cloud Scheduler job at **14:58 SGT daily** POSTs `/orchestrator/daily`
(`main.py`), which runs the whole agent chain in `ops/daily.py`; its ingestion step is the call to
`run_ingestion_pass(get_enabled_sources(), …)`. A single pass can also be triggered on its own via
`POST /pipeline/run?dry_run=…`. Both endpoints require the ops token. The in-process APScheduler
still exists in `main.py` for local development but is **off unless `ENABLE_INPROCESS_SCHEDULER`
is true**, and it now registers exactly one job (the same daily chain) — two places defining one
schedule is what let four agents drift into never running in production at all.
See `docs/AUTONOMY.md` and `CLAUDE.md`.

**Why this was logged here and not designed here:** infrastructure specs rot faster than logic
specs because they change outside the codebase. Entangling the trigger with the ingestion
design would couple two independently-evolving decisions and reintroduce exactly the kind of
stale-spec drift this project already suffers from. The seam is `run_ingestion_pass()`; the
trigger lives behind it and changed without disturbing ingestion logic — which is the evidence
that the seam was worth having.

---

## 2. Architecture overview

```
                          ┌─────────────────────────────────────────────┐
   (trigger — §1, live)   │  run_ingestion_pass(sources, now)            │
   Cloud Scheduler ──HTTP─▶│  — the single entrypoint / orchestrator     │
   or CLI / manual         └───────────────┬─────────────────────────────┘
                                           │  for each enabled Source:
                                           ▼
         ┌───────────────┐   fetch()   ┌──────────────────┐
         │  Source        │◀───────────│  StateStore       │  read watermark
         │  (interface)   │            │  (pipeline_state) │
         └───────┬────────┘            └──────────────────┘
                 │ list[Candidate]
                 ▼
        ┌──────────────────┐  filter by watermark (published_at > last_run)
        │  RecencyFilter   │  → only genuinely-new items survive
        └───────┬──────────┘
                ▼
        ┌──────────────────┐  dedupe by canonical URL against
        │  Deduplicator    │  war_room_queue + incidents (§5.2 — the same checks
        └───────┬──────────┘  as check_duplicate, but raises instead of failing open)
                ▼
        ┌──────────────────┐  Stage 1 (Gemini, quota-guarded) — rejects 60-70%
        │  Stage 1 filter  │  of raw scrape volume
        └───────┬──────────┘
                ▼           (all sources gathered first, then:)
        ┌──────────────────┐  ONE batched Haiku call partitions the pass's
        │  Clustering      │  survivors by real-world EVENT → one draft per STORY
        └───────┬──────────┘  (CLUSTER_BEFORE_WRITE; 'on' in production)
                ▼
        ┌──────────────────┐  Stage 2 (Claude Haiku) → proposed_* fields, then
        │  Stage 2 writer  │  consolidation.check() → build_queue_row()
        └───────┬──────────┘
                ▼
        ┌──────────────────┐  write rows (status='pending' | 'update')
        │  war_room_queue  │  → operator reviews & publishes
        └──────────────────┘

   On any source failure → FallbackLadder (§6) → degraded IngestionReport → War Room alert
   On success → StateStore.update(source, the watermark the pass SETTLED — §5.1, §8)
```

Every box is a single-responsibility unit with an explicit contract. The orchestrator owns
sequencing and failure handling; it owns no fetching or parsing logic itself.

> **Built-reality note.** Clustering was not in the original design. It was added after the
> pipeline went live, because writing one draft per URL produced several near-duplicate
> single-source cards for one story. It sits between Stage 1 and Stage 2, and is the reason the
> Stage-2/write phase is deferred until every source has been fetched. See
> `docs/PIPELINE_CHANGES_2026-07-30.md` §1 — and in particular do not reintroduce pairwise
> judging + union-find, which merged transitively (A~B and B~C merged A, B *and* C with nothing
> ever comparing A to C) and turned three unrelated events into one card.

---

## 3. Data contracts (interfaces before implementations)

### 3.1 `Candidate` (the unit of flow)

A plain, immutable data object. Every Source emits these; nothing downstream cares which Source
produced them.

```python
@dataclass(frozen=True)
class Candidate:
    title: str
    content: str                # summary / snippet (HTML stripped)
    url: str                    # CANONICAL article url — a publisher URL, never a wrapper
    source_name: str            # human-readable, e.g. "Channel NewsAsia"
    source_type: str            # CANONICAL vocabulary: 'msm' | 'signal' | 'rss' | ...
    published_at: date | None   # parsed publication date; None if unknowable
    discovered_via: str         # which Source produced this (e.g. 'straits_times_sitemap')
```

**Contract rules:**
- `url` MUST be the canonical article URL, not a wrapper. Dedupe correctness depends on this:
  `dedup.is_duplicate` matches on the URL, so a wrapper matches nothing and the story looks new
  every pass. **This rule is now enforced, not merely stated** — see the box below.
- `published_at` MUST be parsed from the source's own date field, never inferred from "now."
  If a source cannot supply a date, `published_at = None` and the item is treated
  conservatively (see §5 RecencyFilter).
- `source_type` is normalised at the adapter boundary by
  `classifiers.source_allowlist.canonical_source_type`, so the legacy `'edmw'` spelling never
  reaches downstream code — `'signal'` is the only spelling the `sources` table CHECK accepts,
  and a bare `== 'edmw'` comparison silently breached guardrail #2 once already (QA M14).
- `Candidate` is `frozen` (immutable). Transformations produce new objects.

> **The "canonical, not a wrapper" rule is enforced in three places (2026-08-02).** It was
> written here as a contract with nothing behind it, and a source violated it in production:
> `google_news_rss` emitted `news.google.com/rss/articles/<blob>` URLs that could not always be
> resolved, and the unresolved wrapper was stored as `Candidate.url`. Now:
> 1. **No source produces one.** The aggregator was removed; every remaining adapter reads a
>    publisher's own feed, sitemap or search feed (§3.3).
> 2. **`classifiers/source_allowlist.py` has a `REDIRECT_DOMAINS` frozenset** (news.google.com,
>    google.com, feedproxy.google.com, t.co, bit.ly, apple.news, …) and `is_redirect_domain()`.
>    `classify()` returns `'redirect' | 'signal' | 'approved' | 'unapproved'` and checks redirect
>    **first, without consulting the `sources` table**, so the rule cannot be defeated by adding
>    the host to `sources`. `check_source_urls()` reports the casualties under `dropped_redirect`.
> 3. **`consolidation/queue_row.py` substitutes a real publisher URL** for a redirector
>    `source_url`, or flags the row for the operator when there is none to substitute.
>
> Dropping a redirector can empty `source_urls`. That is intentional and NOT special-cased: a
> candidate whose only citation was a wrapper has no verifiable source, which is exactly the
> state guardrail #1 exists to catch. Guard: `test_source_allowlist.py`,
> `test_ingestion_sources.py`.

### 3.2 `Source` (the pluggable interface)

```python
class Source(Protocol):
    name: str                   # stable id, e.g. 'straits_times_sitemap'; keys pipeline_state
    enabled: bool
    source_type: str            # canonical type this source emits ('msm' | 'signal' | …)

    def fetch(self, since: date | None) -> list[Candidate]:
        """
        Return candidates this source currently offers.
        `since` is the source's last-run watermark (may be None on first run).
        A source MAY use `since` to narrow its query, but MUST NOT rely on it for
        correctness — the orchestrator re-applies the RecencyFilter regardless.
        MUST raise SourceBlockedError on bot-detection / rate-limit, or
        SourceUnavailableError on transient failure. MUST NOT swallow these.
        """
```

**Why `source_type` is declared on the Source and not read off a Candidate:** the orchestrator
writes a `scraper_health` row for a source whose `fetch()` **raised** — the case with no
candidates to inspect, and the one health data actually exists for. A source with no declared
type would go untyped in exactly the rows that matter. `test_ingestion_sources.py` asserts every
registered source declares one.

**Why `since` is advisory, not authoritative:** no source can be trusted to honour it.
The empirical origin of the rule was the Google News RSS smoke test (`scrapers/smoke_test.py`),
which showed the feed returning a relevance-ranked grab-bag spanning years and ignoring date
operators entirely. The rule outlived that source, and the current adapters show why it must:
`LegacyScraperSource` **ignores `since` completely** (those scrapers poll a current feed and have
no date-range query at all), while `NewsSitemapSource` and `WordPressSearchSource` apply it only
as a cost optimisation — to avoid fetching article bodies for entries already covered — and
never drop a dateless entry with it. The orchestrator therefore always re-filters by watermark
(§5.1). Defence-in-depth: the source may optimise with `since`, correctness lives in the
orchestrator.

### 3.3 Concrete adapters: MSM primary, publisher-owned discovery second (Q1 = 1b)

Per the owner decision (§11, Q1 = 1b), **Singapore MSM is the primary source spine**, with a
wider discovery net behind it. All adapters implement the same `Source` protocol. The live
registry is `ingestion/sources/get_enabled_sources()`, which returns **25 sources**.

**Primary — the SG MSM adapters (14 scrapers).** The existing per-source scrapers
(`scrape_cna.py`, `scrape_mothership.py`, `scrape_straitstimes.py`, `scrape_stomp.py`, …) are
wired behind the `Source` interface, almost all through the one shared `LegacyScraperSource`
adapter rather than a wrapper file each. They read each outlet's current feed or listing page:
- **RSS-dated (6):** CNA, Mothership, Straits Times, MustShareNews, The Independent, Yahoo.
- **HTML-dated (6):** AsiaOne, Stomp, Zaobao, Shin Min, Berita Harian, Tamil Murasu — their
  listing pages carry no date, so `scrapers.resolve_published_at()` reads it from the article
  (URL path first, then meta tags).
- **Signal (2):** Reddit and EDMW/HWZ — see below.

**Discovery — the publishers' own sitemaps and search feeds (11 adapters, added 2026-08-02).**
This is the role Google News RSS used to fill, and it is now filled by
`NewsSitemapSource` (9 outlets: `cna_sitemap`, `straits_times_sitemap`, `yahoo_sitemap`,
`asiaone_sitemap`, `stomp_sitemap`, `zaobao_sitemap`, `berita_harian_sitemap`,
`tamil_murasu_sitemap`, `the_independent_sitemap`) and `WordPressSearchSource`
(2 sites: `mustsharenews_search`, `the_independent_search`, over `?s=yishun&feed=rss2`).

Two reasons, in order:

1. **Correctness — `Candidate.url` must be a canonical publisher URL (§3.1).** Google News RSS
   entries link to `news.google.com/rss/articles/<blob>` wrappers, which do **not** HTTP-redirect
   — decoding one needs a reverse-engineered `batchexecute` RPC that Google rotates — so when
   resolution failed the WRAPPER was stored as the article URL. That broke three things at once:
   dedupe (which matches on URL) could not see we already held the story; the wrapper landed in
   `war_room_queue.source_url` and in `source_urls`, citing a redirect instead of the outlet that
   did the reporting; and `source_allowlist` could not classify the host, so the row was held
   back as `unapproved_source_domain`. **All three fired in production on 2026-08-01** — two rows
   proposing "updates" to an incident we already had, each citing an unresolved wrapper, each a
   duplicate of a Stomp article the Stomp scraper had ingested cleanly the day before.
2. **Reach.** A news sitemap is a much wider window than a front-page feed, measured 2026-08-02:
   Straits Times 462 sitemap entries vs 44 in RSS; Yahoo 204 vs 5; CNA 50 vs 33. The ST feed
   carried zero Yishun items that morning while its sitemap carried one. A once-a-day pass
   against a 44-entry feed cannot see a story that scrolled out of the window — and that, not a
   broken scraper, is why the fleet kept reporting zeros.

Both discovery families emit the **publisher's own URL and the publisher's own date**, so their
output dedupes cleanly against the primary scrapers instead of manufacturing "update" proposals
for stories already held. Sitemaps carry no body text, so for the handful of keyword-matching
entries (0–3/day across the whole fleet) the article is fetched — **after** the recency check,
never before, which is the lesson Google News taught expensively: resolving first burned ~600
round-trips a pass on entries the recency filter discarded seconds later.

Not covered, and why: **Mothership** has no news sitemap (`/sitemap.xml` re-serves `/feed/`) and
ignores `?s=`, so its front-page feed is the ceiling for that publisher; **Shin Min** serves
neither robots.txt nor a sitemap.

**Signal — Reddit and EDMW/HWZ.** Registered as `source_type='signal'`: corroboration count
only, never a quoted source (guardrail #2), and never the event date. MSM is the sole authority
for both the citation and the date. Reddit joined this tier in July 2026 — it is user-generated
discussion, not verifiable journalism, and a thread reviving an old case carries a recent post
date, which manufactured duplicate cards for old events.

All adapters are interchangeable behind the protocol; adding/removing one is a config change, not
an orchestrator change.

> **Do not add an aggregator or a redirect wrapper to this registry.** Not Google News, not a
> link shortener, not a "future `SerpApiSource`" — the protocol would accept one, and that is
> precisely the hole. `test_ingestion_sources.py` pins it: no registered source may have a
> `google` in its name, none may sit on a `REDIRECT_DOMAINS` host, and the registry must equal
> exactly the 14 scrapers plus the 11 discovery adapters — so a future "cleanup" cannot quietly
> drop a publisher either.

> Implementation sequencing note: the MSM adapters were brought online incrementally (the most
> reliable RSS feeds — CNA, Mothership — first, then the HTML-scraped outlets), but the
> architecture treats MSM as primary from day one per the owner decision. Do not invert this to
> "aggregator primary" — that was an earlier, rejected framing, and the 2026-08-01 rows are what
> it cost the second time.

**Keyword scope (`scrapers.YISHUN_KEYWORDS`).** Every adapter filters on the same list, so it is
part of the source contract: `yishun`, `khatib`, `chong pang`, `northpoint`, `khoo teck puat`.
Matching is plain case-insensitive substring, so bare `yishun` already covers "Yishun Ring Road",
"Yishun Ave 6" and friends — only names that do *not* contain "yishun" need their own entry.
`sembawang` is **not** on the list: it is a separate URA planning area and never Yishun (it sat
there from the first commit anyway, while every TechSpec from v1.5 claimed it had been removed).
`nee soon` is deliberately excluded from the English list — in news copy it reads as the
constituency, which imports exactly the political content guardrail #4 must reject — but is
retained in the Malay list, where it is a place name. Guard: `test_yishun_geography.py`.

---

## 4. The entrypoint (the drift-resistant seam)

```python
def run_ingestion_pass(
    sources: list[Source],
    now: datetime,
    *,
    dry_run: bool = False,
    max_duration_seconds: int = 1200,   # pass deadline, monotonic clock
    circuit_breaker_n: int = 5,         # consecutive same-class API errors -> abort
    activity=None,                      # optional ops activity logger (agent_events)
) -> IngestionReport:
    """
    Execute ONE ingestion pass across all enabled sources.

    This is the SINGLE entrypoint. Cloud Scheduler, a CLI command, a manual
    re-run button, or a test harness all call this identically. It owns no
    knowledge of how it was triggered.

    Steps per source:
      1. watermark = StateStore.get(source.name)
      2. candidates = source.fetch(since=watermark)          # may raise
      3. fresh = RecencyFilter(candidates, watermark, now)   # §5.1
      4. novel = Deduplicator(fresh)                         # §5.2
      5. for each novel candidate:
           a. Stage1 (quota-guarded); a rejection is a VERDICT (§5.1)
           b. gather the survivor — Stage 2 is deferred to the cluster phase
      6. after every source: cluster the gathered candidates (one batched Haiku
         call), then per cluster:
           a. Stage2 -> draft, written ONCE per story with all its sources
           b. result = consolidation.check(draft)             # §5.4 (shared module)
           c. row = build_queue_row(item, draft, result)      # §5.4 (shared builder)
           d. write row to war_room_queue (new / update)
           e. check_milestones() — NON-FATAL herald/milestone check after the
              queue insert (preserves milestone detection from the retired
              LangGraph graph; try/except so it never disrupts the pass)
      7. StateStore.update(source.name, WatermarkTracker.value())   # §5.1, §8
    On SourceBlockedError / SourceUnavailableError -> FallbackLadder (§6).
    On infrastructure failure (DB unreachable during dedup/queue-write) ->
      abort pass as DEGRADED with infra_error (§5.2, §7).
    Always returns an IngestionReport (§7); never raises to the caller.
    """
```

> **Built-reality notes (post-implementation reconciliation):**
> - **The write phase is deferred and clustered (steps 5b/6).** The design wrote one draft per
>   candidate; the shipped pass gathers every Stage-1 survivor across all sources, partitions them
>   by real-world event with ONE batched Haiku call, and writes one row per STORY with all its
>   sources. `CLUSTER_BEFORE_WRITE` gates it (`off` | `shadow` | `on`); the code default is `off`,
>   production is pinned to `on` in `infra/cloudbuild.yaml`. A single-member cluster produces
>   byte-identical output to the old per-candidate path.
> - **The pass carries safety rails the design did not specify**, all reflected in the signature:
>   a monotonic-clock deadline (`max_duration_seconds`, checked before each source AND inside the
>   candidate loop), a circuit breaker on consecutive same-class API errors
>   (`circuit_breaker_n`), and a Stage 1 daily-quota halt. Each of them can end a pass early,
>   which is exactly why the watermark is settled per-candidate rather than per-source (§5.1).
> - **Herald/milestone (step 6e)** was NOT in the original design but IS required: the retired
>   LangGraph graph ran `herald_agent.check_milestones()` after every queue insert. Dropping it
>   would have silently killed milestone detection, so it is ported into `run_ingestion_pass()`
>   as a non-fatal call. `herald_agent.py` is kept (also used by `backfill_agent.py`); only the
>   LangGraph graph file is deleted.
> - **`phenomenon_count` is structurally 0 in the IngestionReport.** The shipped
>   `consolidation/check.py` returns `action='new'|'update'|'skip'` only — it does NOT implement
>   the design's aspirational `kind='phenomenon_member'` auto-detection. Phenomenon/umbrella-hub
>   *detection* is the job of the existing `pattern_detection.py` agent, NOT the ingestion
>   orchestrator. The orchestrator routes on `action` (new/update); phenomenon membership is
>   surfaced for the operator via `raw_content.agent_related_incidents` on weak matches. This is
>   a deliberate separation of concerns, not a missing feature — ingestion proposes; pattern
>   detection (separately) discovers umbrellas. `phenomenon_count` remains in the report schema
>   for the day phenomenon routing is unified, but reads 0 today.

**Contract guarantees:**
- Pure function of `(sources, now)` plus external state (DB, watermarks). No hidden globals.
- Never raises to its caller — all failures are captured in the returned `IngestionReport`.
  (A trigger must always get a clean response, even on total failure, so it can log/alert.)
- `dry_run=True` runs the full fetch → recency → dedup → Stage 1 → Stage 2 → consolidation path
  and writes NOTHING: no queue rows, no `incident_links`, no watermark advance, no
  `pipeline_run_history` row, no `scraper_health` row, no budget persistence. For testing and the
  smoke-test lineage.
- Watermark advances **only** for sources that completed successfully, and then only as far as
  what the pass actually SETTLED (§5.1). A blocked source keeps its old watermark so the next run
  retries the same window — no data is skipped due to a block.

---

## 5. RecencyFilter & Deduplicator

### 5.1 RecencyFilter
```
keep candidate IF:
    candidate.published_at is not None
    AND candidate.published_at > watermark        (strictly newer than last successful run)
DROP (but COUNT) candidate IF:
    published_at <= watermark                     (already covered by a prior run)
ROUTE-TO-REVIEW candidate IF:
    published_at is None                           (undateable — route to War Room with a
                                                    'dateless' flag. The operator does the
                                                    due-diligence a machine cannot: googles it,
                                                    finds the real source, records the decision.
                                                    That human sourcing work IS training signal —
                                                    see docs/LEARNING_LOOP.md §2.1. War Room is the
                                                    sourcing-model's training-data generator, not
                                                    just an approval gate. Operator decision = Q2/2b.)
```
A dateless item that routes to review and is approved gets its date set by the operator at that
point; one that is rejected becomes a labeled negative example. Either way the signal is captured.
First run (watermark is None): no dated candidate is dropped — see §5.3, the cold-start concept is
NOT a "last N days" lookback. The operator's model is a three-phase one (Historical/Cold+Warm,
then Futurist/Forward). Read §5.3 before changing first-run behaviour.

> **What sets the watermark this filter reads: DECISIONS, not writes** (`ingestion/watermark.py`,
> added after the pipeline went live). §8's rule is "max `published_at` actually ingested", and
> the orchestrator originally read *ingested* as *written to `war_room_queue`* — which is much
> narrower and cost money daily. A Stage 1 rejection (60–70% of raw volume) and a consolidation
> duplicate-skip are **verdicts**, and neither writes a row, so `dedup.is_duplicate` — which reads
> only `war_room_queue.source_url` and `incidents.source_urls` — can never see them again either.
> The watermark is the only thing that can. So each source gets a `WatermarkTracker`, and **every
> `continue`/`break` in the candidate loop must settle it exactly once**: `decided()` for a
> verdict, `unresolved()` for an interruption (error, deadline, budget halt, a gathered candidate
> the cluster phase never reached). Marking neither either loses the story or re-buys its Gemini +
> Haiku calls every day.
>
> Two holdbacks make advancing-on-decisions safe, and neither is decoration — the filter above
> drops `published_at <= watermark`, so the watermark is a date-granular guillotine:
> - **Retry floor** — only decided dates strictly *below* the earliest unresolved date advance.
>   Without it a candidate that hit a transient error is silently dropped by its own
>   successfully-decided siblings.
> - **Same-day grace** — never advance onto the pass's own date. The pass runs once (14:58 SGT)
>   and the source publishes all day; advancing to today would drop everything published after the
>   pass ran, unseen and unlogged. Costs at most one extra pass per article.
>
> Dateless candidates move the watermark in neither direction. Guard: `test_watermark_advance.py`.
> Full account: `docs/PIPELINE_CHANGES_2026-07-30.md` §9.

### 5.2 Deduplicator
`ingestion/dedup.py` performs the same canonical-URL checks as `check_duplicate(url)`
(corroboration.py) — `war_room_queue.source_url` and `incidents.source_urls` — but is a separate
implementation rather than a call into it, for the infra-failure reason in the box below. Within a
single pass, candidates are also deduped against each other by URL (the in-run `seen_urls` set).

**No fuzzy/title dedup in v1.** URL-exact only, matching current behaviour. Title/semantic
dedup is explicitly deferred (see §9) to avoid over-engineering and false merges.

> **Infra-failure handling (review S1) — built as specified.** `check_duplicate()` fails OPEN
> (returns `False`) on a Supabase error — i.e. treats an item as novel when it cannot verify. That
> is fine for its single-lookup callers but dangerous at pass scale: during a Supabase outage
> *every* item looks novel and the subsequent queue-writes also fail. So `dedup.is_duplicate()`
> raises **`dedup.InfraError`** instead, and the orchestrator distinguishes source-fetch failures
> (handled per-source by the FallbackLadder, §6) from *infrastructure* failures (DB unreachable
> during dedup or queue-write). On an `InfraError` the **entire pass aborts as DEGRADED** — it
> does not continue treating everything as novel — and the returned `IngestionReport` records
> `infra_error` distinctly from source skips. This is the one reason dedup is not a thin wrapper
> over `check_duplicate`: the two need opposite failure modes.

---

### 5.3 Cold Start / Warm Start / Forward — the three-phase model (CORRECTED)

> ⚠️ An earlier draft of this design treated "cold start" as a 7-day first-run lookback. That was
> wrong. The operator's actual model, now authoritative:

- **Cold Start (1980–2023) = the hand-built historical archive as a PROTOTYPE/training set.**
  Not a scraping window. The **Historical agent's** job here is enrichment & discovery against the
  existing verified cards: cross-check their source links, hunt *additional* corroborating
  sources, enrich existing stories with proof the manual pass missed, and detect new items that
  belong under an existing umbrella (more Kurt Tay, more cat-killer incidents). The principle is
  **"find more proof, enrich the existing story, or discover new sources/trends under the same
  umbrella"** — working from a known spine, not bulk-ingesting history (which was proven
  impossible).
- **Warm Start (2024–Jun 2026) = the litmus-test window.** The operator has Google-search-based
  coverage 1980–2025; the Futurist agent is put to the test scraping **Dec 2025 → now (Jun 2026)**
  to see what it surfaces, which the operator then validates in War Room — the first real
  exercise of the Learning Loop (see `docs/LEARNING_LOOP.md`). Denser data than Cold Start.
- **Forward (Jun 2026 →) = the daily live pipeline.** `run_ingestion_pass()` as specified in this
  doc. SG MSM primary, publisher-owned discovery second (Q1 = 1b, §3.3). This is the **Futurist
  agent**, the subject of the Learning Loop.

The `run_ingestion_pass()` machinery in this document is the **Forward / Futurist** pipeline. The
Historical agent (Cold + Warm enrichment/discovery) is a **distinct agent** closer in character to
the manual consolidation work — it reads the archive and proposes enrichments/links for human
review, rather than running the live scrape pipeline. It is specified separately (TechSpec §4.x /
a future `HISTORICAL_AGENT_DESIGN.md`); it is NOT a mode of `run_ingestion_pass()`.

First-run behaviour as built: **there is no lookback constant.** A proposed
`FORWARD_FIRST_RUN_LOOKBACK_DAYS` was never implemented and does not exist in the code. When a
source has no `pipeline_state` row, `RecencyFilter` receives `watermark=None` and keeps every
dated candidate the source offers — the window is whatever that source's feed or sitemap serves.
In practice this is self-limiting (a front-page feed reaches back days; a news sitemap a few
hundred entries) and the pass deadline bounds the rest. Either way it is a narrow implementation
detail, not the meaning of "cold start."

### 5.4 Consolidation routing — new vs. update vs. phenomenon (review B2)

> This section closes a real gap: candidates are NOT always new incidents. A forward pipeline
> especially must handle **new reports on existing stories** (e.g. a fresh article on the Koh Ah
> Hwee or 2024 Ring Road developing cases) by **enriching the existing card's timeline**, not by
> creating a duplicate. The four operator requirements below are consolidation behaviours and are
> enforced by ONE shared module both agents call.

**Operator requirements this routing must satisfy (authoritative):**
1. Curate/validate as much older news as possible — Historical agent and Futurist agent run the
   **same** consolidation logic.
2. Duplicate news from different sources on the same timeline must **reinforce** (corroboration
   count up), not duplicate.
3. A later report linked to the first report must **enrich the card with a timeline entry**
   (append to `source_timeline`), **not merely tag a link**.
4. Different news about the same recurring incident/person is classified together as a
   **phenomenon** (umbrella hub) or a **person of interest** — not as disconnected cards.

**The shared capability:** a `consolidation` module (see §10 layout) exposes:

```python
# AS DESIGNED (aspirational — see the "as built" note below)
def check(candidate: Candidate, draft: Stage2Draft) -> ConsolidationResult: ...

@dataclass(frozen=True)
class ConsolidationResult:
    kind: str                       # 'new' | 'update' | 'phenomenon_member'
    matched_incident_id: str | None # set for 'update' and 'phenomenon_member'
    proposed_role: str | None       # source_timeline role: initial|update|verdict|...
    phenomenon_hub_id: str | None   # umbrella card id, if this belongs under one
    corroborates: bool              # True if this reinforces an existing source/timeline entry
    reason: str                     # human-readable, recorded for War Room + training signal
```

> **As built (`consolidation/check.py`).** The shipped result is narrower, and the field names
> differ — read the code, not the block above, when writing against it:
> ```python
> def check(candidate: dict, supabase_client=None) -> ConsolidationResult: ...
>
> @dataclass                       # not frozen
> class ConsolidationResult:
>     action: str                  # 'new' | 'update' | 'skip'   (no 'phenomenon_member')
>     matched_incident_id: str | None
>     related_incidents: list[RelatedLink] = []
>     queue_status: str = "pending"        # 'pending' | 'update'
>     match_confidence: float = 0.0
>     match_reason: str = ""
>     agent_role_proposed: str = "initial" # 'initial' | 'update' | 'follow_up' | 'skip'
> ```
> `action='skip'` (an exact duplicate of a row already awaiting review) is a case the design did
> not name; the orchestrator treats it as a verdict and drops the candidate. Phenomenon routing is
> the deliberate omission — see the §4 built-reality note.

**Routing the result into a queue row (the single shared builder):**

```python
# AS BUILT (consolidation/queue_row.py)
def build_queue_row(item: dict, draft: dict,
                    consolidation: ConsolidationResult | None = None,
                    is_update: bool = False, date_missing: bool = False,
                    edmw_signal_count: int = 0,
                    include_related_incidents: bool = False,
                    is_backfill: bool = True) -> dict:
    # ONE correct builder. Replaces the inline _build_queue_row in
    # backfill_agent.py (extracted) AND the dead pipeline.py::_build_queue_row
    # (mined then deleted). Candidate->dict conversion (dataclasses.asdict)
    # happens in the ORCHESTRATOR's _candidate_to_item(), at the boundary
    # (fixes review S3 — the frozen Candidate dataclass must not be **spread
    # directly), and `published_at` becomes a JSON-safe "date" string there.
```

> **`is_backfill` is load-bearing (QA H4).** The War Room buckets bulk-backfill actions on
> `raw_content._backfill`, so the forward orchestrator MUST pass `is_backfill=False` — otherwise
> live drafts can be mass-approved as "historical cleanup". It defaults to `True` for the
> backfill agent's benefit, which makes the forward caller's explicit `False` the only thing
> standing between the two populations.

> **The shared builder must cover BOTH backfill and forward needs (mined from pipeline.py).** The
> builder extracted from `backfill_agent.py` lacked two fields the **forward** (Futurist) pipeline
> requires, because backfill never needed them:
> - `edmw_signal_count` — forward candidates can carry EDMW forum signal (backfill never does).
>   Defaults to 0; set by the orchestrator for signal-bearing candidates.
> - `agent_related_incidents` in `raw_content` — the related-incident metadata that War Room
>   renders. `pipeline.py` wrote this from `result.related_incidents`; the backfill builder
>   dropped it. The shared builder MUST surface it so the operator sees proposed links.
> These are folded in from `pipeline.py`'s (dead) builder, which already handled them correctly.
> After folding, `pipeline.py` is deleted (§0, §10b).

- **`action='new'`** → standard pending queue row (`status='pending'`), no `update_target_incident_id`.
- **`action='update'`** → queue row with `status='update'` and `update_target_incident_id` set, so
  the operator approves an **append to the matched card's `source_timeline`** (requirement 3:
  enrich the timeline, do not just tag a link). `agent_role_proposed` carries the timeline role,
  and the orchestrator writes the `incident_links` rows immediately on insert.
- **`action='skip'`** → the candidate is an exact duplicate of a row already awaiting review. It
  is dropped, and the drop is a *verdict*: the watermark advances past it (§5.1), because
  re-running the pass would only buy the same Stage 2 draft and the same judgement again.
- **`kind='phenomenon_member'` was NOT built.** Phenomenon/umbrella-hub *detection* belongs to
  `pattern_detection.py`, not to ingestion (see the §4 built-reality note). What ingestion does
  instead is surface `consolidation.related_incidents` as `raw_content.agent_related_incidents`,
  so the operator sees the proposed links and decides — which is also what the defamation guard
  (CONSOLIDATION_RULES.md rule 11) wants for requirement 4.
- **Corroboration (requirement 2) is arithmetic, not a flag.** There is no `corroborates` field.
  `build_queue_row` sets `corroboration_count` to the number of *distinct non-signal* source URLs
  actually backing the draft (`max(1, …)`). It was hardcoded to 1, so multi-source stories reached
  the operator claiming a single source — which also zeroed the frontend lightning meter, since
  `bolts = corroboration_count − 1`.

**This module IS the executable form of `docs/CONSOLIDATION_RULES.md`.** The rules we wrote and
validated by hand this session become the logic BOTH the Historical agent (`backfill_agent.py`,
refactored) and the Futurist agent (`ingestion/orchestrator.py`) execute — one implementation,
identical behaviour, no duplication, no path inheriting the behaviour "by accident."

> **Why this is extracted, not reused-in-place (the B2-b decision).** Reusing
> `backfill_agent.py`'s private `_build_queue_row`/`_apply_tier` from the ingestion layer would
> couple the live pipeline to backfill internals — debt incurred for a short-term gain, and the
> exact entanglement that produced the §B3 "recent path" bug. Instead the consolidation +
> row-building logic is EXTRACTED into a shared module; `backfill_agent.py` is refactored to call
> it (removing duplication, not adding a parallel path). Clean, reusable, single source of truth.

---

## 6. FallbackLadder (the "when blocked, then what" state machine)

Explicit states, not scattered try/excepts. Per source:

```
NORMAL ──fetch ok──────────────────────────────▶ done, status='ok' (advance watermark)
   │
   ├─ SourceUnavailableError (transient) ──▶ BACKOFF
   │                                          wait BACKOFF_SECONDS (30), retry ONCE
   │                                          ├─ ok ─▶ done, status='ok'
   │                                          └─ fail ─▶ SKIP_SOURCE
   │                                                     status='unavailable' | 'blocked'
   │
   └─ SourceBlockedError (bot trap) ───────▶ SKIP_SOURCE immediately, status='blocked'
                                              (do NOT retry into a ban; matches smoke-test policy)

SKIP_SOURCE: record reason, leave watermark UNCHANGED, mark the pass DEGRADED,
             continue to next source.
```

`run_with_fallback(source_name, fetch, backoff_seconds)` is pure state logic: it takes a zero-arg
callable, returns `(candidates | None, SourceResult)`, and touches no DB. `None` means SKIP_SOURCE.
Two details that are not decoration:

- **The orchestrator passes `backoff_seconds=0` when the pass deadline is near** (specifically when
  less than twice the backoff remains). With 25 sources, a 30 s backoff each is over 12 minutes of
  pure sleep — which used to be spent entirely outside the deadline check and could push a pass
  past its budget without a single candidate being processed. Don't spend the retry wait if there
  is no time left to use the result.
- **`SourceResult.duration_ms` times the ATTEMPT, never the sleep.** On the retry path the clock
  restarts *after* the backoff. `scraper_health`'s slow-source check compares a run against that
  source's own 7-day average, so charging a 30 s sleep to the source would dwarf the fetch and
  turn every retry into a phantom "slow source" for a week.

**After all sources processed:** if ANY source ended in SKIP_SOURCE, the pass is **DEGRADED**.
A degraded pass:
- still queues whatever the healthy sources produced (partial success is fine),
- sets `IngestionReport.degraded` and records the per-source reason in `per_source` (there is no
  separate `DegradedRunReport` type — the report *is* the degraded run report),
- writes an `agent_events` row (`anomaly` for a block, `warning` for unavailable) and a
  `scraper_health` row with `status='error'` — the red dot in the War Room health view, and what
  `ops/supervisor.py` and `ops/maintenance.py` read to tell "a bad day" from "dead for a week",
- is recorded in `pipeline_run_history` (§8) and in that source's `pipeline_state` row.

**Critical invariant:** a blocked source NEVER advances its watermark, so no window is ever
skipped because of a block. The next run re-attempts the same `since`. Note the orchestrator
still writes `pipeline_state` for a skipped source — with the *previous, unchanged* watermark and
the failure status — so a blocked source is visibly blocked rather than merely stale.

**A skipped source gets no zero-item `scraper_health` row.** It is written only for a source the
pass actually fetched: a zero-item row for a source that never ran would read as a genuine quiet
run and walk it toward a false zero-streak. (That streak threshold is 30 consecutive passes, not
3 — `items_found` counts keyword-surviving candidates rather than articles served, and at 3
nearly the whole fleet sat permanently at `warning`. It is a display signal only; outage
*alerting* derives from `pipeline_run_history`, never from `scraper_health`.)

**No "spider around the block" logic.** Per this session's findings, an endpoint that
structurally lacks data cannot be coaxed into yielding it by retry tricks; and scraping that
trips bot-detection must back off, not escalate. The ladder's job is graceful degradation +
honest reporting, not evasion.

---

## 7. IngestionReport (no silent failure)

Every pass returns this, and it is logged + (if degraded) surfaced to War Room:

```python
@dataclass
class IngestionReport:
    started_at: datetime
    finished_at: datetime
    dry_run: bool
    per_source: list[SourceResult]     # name, status, fetched, fresh, novel, queued,
                                       #   reason, duration_ms (the attempt, not the backoff)
    total_queued: int
    new_count: int                     # action='new' rows
    update_count: int                  # action='update' rows (timeline enrichments proposed)
    phenomenon_count: int              # structurally 0 — see the §4 built-reality note
    degraded: bool                     # True if any source was skipped
    infra_error: str | None            # set if the pass aborted on a DB/infrastructure failure
    notes: list[str]
```

A pass that queues zero items is only "healthy-quiet" if every source was NORMAL and simply had
no new items. A pass that queues zero because a source was blocked is DEGRADED and says so. The
distinction is the whole point of goal #2.

`notes` is where the honest accounting lands and is worth reading on a real run: candidates
dropped below the watermark, watermarks **held back** by the retry floor (`WatermarkTracker
.hold_note()`, emitted only when a hold actually cost something), consolidation duplicate-skips
and what they cost, rate-limiter sleep totals, the cluster-write summary, and the abort reason if
the pass ended early.

---

## 8. New schema: `pipeline_state` table

The spec had **no** watermark/state mechanism (confirmed at design time: no `pipeline_state`,
`watermark`, `last_run`, or `last_seen` anywhere). This is the one genuinely new table Option B
required. **Shipped in migration `006_phase1_apply_now.sql`**, as written below plus
`ENABLE ROW LEVEL SECURITY` on both tables (no policy → service-role only) and a
`ran_at DESC` index on the history table.

```sql
CREATE TABLE pipeline_state (
  source_name        TEXT PRIMARY KEY,          -- matches Source.name, e.g. 'straits_times'
  last_run_at        TIMESTAMPTZ,               -- when this source last completed successfully
  watermark          DATE,                      -- max published_at successfully ingested
  last_status        TEXT NOT NULL DEFAULT 'never_run'
                       CHECK (last_status IN ('never_run','ok','degraded','blocked','unavailable')),
  last_reason        TEXT,                      -- failure detail when not 'ok'
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Optional lightweight run-history for observability (append-only, capped/pruned):
CREATE TABLE pipeline_run_history (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  ran_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  dry_run       BOOLEAN NOT NULL DEFAULT FALSE,
  degraded      BOOLEAN NOT NULL DEFAULT FALSE,
  total_queued  INTEGER NOT NULL DEFAULT 0,
  report        JSONB NOT NULL               -- the full IngestionReport
);
```

**Watermark write rule:** updated only on a source's successful pass, to the max `published_at`
actually ingested (not to "now" — using "now" would skip items published-but-not-yet-indexed).
"Actually ingested" means **decided**, not **written**: see the box in §5.1 and
`ingestion/watermark.py`. `state_store.update()` writes whatever the tracker hands it and holds no
policy of its own; `last_run_at` moves only when `status='ok'`.

`consecutive_failures` enables a future health policy (e.g. alert louder after N failures) but
v1 only records it; no auto-disable (that would be silent failure of a different kind). It is
read, then reset to 0 on `'ok'` or incremented otherwise.

**`pipeline_run_history` is the alerting substrate, not `scraper_health`.** `ops/supervisor.py`
derives its zero-streak alerts from this table (one row per real pass, written by
`state_store.record_run`) precisely because an append-only table that stops being appended to
looks exactly like a healthy quiet one — and that is the failure the supervisor exists to catch.
`scraper_health` powers the War Room health views and the maintenance digest. If you need a new
*alert*, base it on run history.

---

## 9. Explicitly NOT in this design (anti-scope-creep)

To keep Option B reviewable and drift-resistant, the following are **named and deferred**:
- **The trigger infrastructure** (Cloud Scheduler/IAM/HTTP endpoint) — §1. Built as a separate
  task, exactly as the seam intended; ✅ done.
- **Fuzzy / semantic dedup** — URL-exact only in v1. Story-level grouping arrived instead, as a
  *clustering* step before Stage 2 (§2), not as a change to dedup.
- **Additional source adapters** — the interface supports them, **but not aggregators**: no
  SerpAPI, no Bing, no Google News. A source must emit the publisher's own canonical URL (§3.1,
  §3.3), and `test_ingestion_sources.py` enforces it.
- **Auto-disabling unhealthy sources** — recorded, not acted on, to avoid silent coverage loss.
- **The `people_profiles` / entity-hub system** (spec §4.x, v1.6) — orthogonal; not touched here.
- **Pixel-art generation** — downstream of queue approval (it now runs in the War Room approve
  route), unrelated to ingestion.
- **The Historical agent (Cold/Warm enrichment & discovery)** — a DISTINCT agent (§5.3), not a
  mode of `run_ingestion_pass()`. Specified separately.
- **Learning Loop Phases 2 & 3** (graduated autonomy; LoRA fine-tuning) — designed/roadmapped in
  `docs/LEARNING_LOOP.md`, explicitly NOT built now. **Phase 1 (contextual learning) IS in scope**
  and the Futurist agent reads `source_reputation` + `training_signals` per run.

---

## 10. File layout (where the code will live)

Per the established convention (`scrapers/` = source-fetching, `classifiers/` = scoring,
`orchestrator/` = sequencing):

```
packages/agents/
├── consolidation/                  # SHARED module — used by BOTH agents (review B2-b)
│   ├── __init__.py
│   ├── rules.py                    # executable form of docs/CONSOLIDATION_RULES.md
│   ├── check.py                    # consolidation.check() -> ConsolidationResult
│   │                               #   new / update / phenomenon_member routing
│   └── queue_row.py                # build_queue_row(candidate, draft, result) — ONE builder,
│                                   #   Candidate->dict conversion at this boundary (fixes S3)
├── ingestion/                      # NEW package — the Option B Futurist layer
│   ├── __init__.py
│   ├── contracts.py                # Candidate, Source protocol, errors, IngestionReport
│   ├── recency.py                  # RecencyFilter
│   ├── dedup.py                    # thin wrapper over existing check_duplicate (+ infra-fail rule)
│   ├── state_store.py              # pipeline_state read/write
│   ├── fallback.py                 # FallbackLadder state machine
│   ├── budget.py                   # GroqBudget seeding/persistence wrapper (see S2 note below)
│   ├── learning.py                 # Phase-1 contextual: read source_reputation +
│   │                               #   training_signals patterns, steer prompts/scoring
│   ├── orchestrator.py             # run_ingestion_pass() — the entrypoint; calls consolidation/
│   └── sources/
│       ├── __init__.py
│       ├── google_news_rss.py      # GoogleNewsRSSSource (corroboration; smoke-test config)
│       └── msm/                    # PRIMARY adapters — wrap existing scrapers behind Source
│           ├── __init__.py
│           ├── cna.py              # wraps scrape_cna.py
│           ├── mothership.py       # wraps scrape_mothership.py
│           └── ...                 # straitstimes, stomp, etc. (incremental)
└── scrapers/
    └── backfill_agent.py           # REFACTORED: its inline _build_queue_row / consolidation
                                    #   logic is removed and it now CALLS consolidation/ (the
                                    #   shared module). No duplicated path; backfill output rows
                                    #   must remain byte-identical after the refactor (verify).
```

Note: the `msm/` adapters **wrap** the existing `scrapers/scrape_*.py` files behind the `Source`
interface rather than rewriting them — reuse over reinvention. The Learning-Loop Phase-1 logic
lives in `ingestion/learning.py` and is read by the orchestrator each run.

> **Stage 1 budget seeding (review S2).** The quota object is per-instance and in-memory with no
> cross-invocation persistence, so a true per-DAY limit only holds if it is seeded from a persisted
> daily counter. `ingestion/budget.py` wraps it to **seed from and persist a per-day counter**
> (keyed by SGT date), so a daily run plus any manual same-day re-run share one budget rather than
> each starting fresh. (Q4 cadence = daily makes the single-run case correct already; this
> hardening covers same-day re-runs without hardcoding the assumption.)
>
> ⚠️ **Two details here changed with the July-2026 Gemini migration.** This note originally named
> `groq_budget.py` and a 500k tokens-per-day ceiling. That file was deleted; `ingestion/budget.py`
> now imports `Stage1DailyQuota` and `RPD_HARD_LIMIT` from `filters/stage1_quota.py`, and the
> constraint is **requests per day** (`STAGE1_RPD`, default 1500), not tokens. The seeding design
> below is unchanged — only what it wraps, and what it counts.

---

## 10b. Build order (implementation sequence — review B1)

Strict dependency order. Each step is verifiable before the next begins.

1. **DB migration FIRST.** Create `pipeline_state`, `pipeline_run_history` (§8), and the Learning
   Loop tables `training_signals` (extend §3.4) + `source_reputation` (`LEARNING_LOOP.md` §2).
   Nothing in `ingestion/` can be built before `state_store.py` has tables to read/write. This is
   a hard build-order dependency — the design's "one genuinely new table" must exist as a real
   migration in `packages/db/`, not just prose in the spec. **[DONE — migration 006.]**
2. **Extract the shared `consolidation/` module** (§5.4, §10) from `backfill_agent.py`'s inline
   logic. Refactor `backfill_agent.py` to call it. **Verify backfill output rows are unchanged**
   (byte-identical) after the refactor before proceeding. **[DONE — rows verified identical.]**
3. **Mark `run_backfill()`'s "recent" path deprecated** (review B3) so no one runs the broken path
   expecting a recency check.
4. **Mine `pipeline.py` into the shared module, then DELETE it (Path A).** Fold its
   consolidation-routing semantics (`action=='update'` → `status='update'` +
   `update_target_incident_id` + immediate `write_incident_links`; `action=='skip'` → drop as
   duplicate) and its EDMW-aware / related-incidents row fields into `consolidation/` (§5.4).
   Verify the shared builder now produces correct rows for BOTH backfill and forward inputs. Then
   **delete `pipeline.py`** — it is dead code and must not remain.
5. `ingestion/contracts.py` (Candidate, Source, errors, IngestionReport) — pure types, no deps.
6. `ingestion/state_store.py`, `recency.py`, `dedup.py`, `budget.py`, `fallback.py` — leaf units.
7. `ingestion/sources/google_news_rss.py` (corroboration) + first `msm/` adapters (primary).
8. `ingestion/learning.py` (Phase-1 contextual read-back).
9. `ingestion/orchestrator.py` (`run_ingestion_pass()`) — wires it all; calls `consolidation/`.
   This is the module that **supersedes `run_graph()`**.
10. **Retire the LangGraph graph (Path A cutover).** Repoint `main.py` from
    `orchestrator/orchestrator.py::run_graph()` to `run_ingestion_pass()` (via the §1 trigger:
    Cloud Scheduler → HTTP, or CLI for first tests). Then **delete `orchestrator/orchestrator.py`**
    (the 6-node LangGraph graph). End state: ONE forward pipeline.
11. CLI/manual entrypoint for testing (dry-run). Cloud Scheduler trigger is the SEPARATE
    deployment task (§1).

### Deprecation directive — `run_backfill()` "recent" path (review B3)

The default (non-`--historical-search`) `run_backfill()` path calls `_scrape_gnews_year` with
hardcoded `after:`/`before:` date operators (e.g. `before:2026-05-31`), which (a) **break the
Google News RSS feed** (proven by the smoke test) and (b) are **hardcoded to a stale date**, so as
of mid-2026 the "recent" path silently excludes the most recent content. It also has no watermark
and re-scrapes the full year range every run.

**This path must be marked deprecated and guarded** so it cannot be mistaken for the recency
pipeline: on invocation it should log a loud deprecation warning and refuse to run (or require an
explicit `--force-deprecated` flag) until it is removed. The forward/recency job is
`run_ingestion_pass()` (§4), NOT `run_backfill()`. The historical (`--historical-search`) and
`--wikipedia-only` paths are unaffected and remain valid for the Historical agent.

### Nice-to-haves folded in (review N1–N3)

- **N1 — corroboration semantics.** In v1, `corroboration_count` reinforcement happens via the
  consolidation `corroborates` flag (§5.4, requirement 2). Auto-incrementing across MSM + Google
  News candidates for the *same* story relies on URL-exact dedup + the consolidation match; true
  cross-source semantic corroboration (same story, different URLs) is part of phenomenon/update
  matching, not naive URL equality. Documented so the limitation is explicit, not assumed.
- **N2 — `groq_session_usage.json` path.** Replaced by the persisted per-day counter in
  `ingestion/budget.py` (S2); the deprecated `run_backfill()` keeps its own file, and since it is
  now guarded off, no concurrent-write collision can occur.
- **N3 — smoke-test provenance.** The "no date operators" finding traces to
  `scrapers/smoke_test.py` (run from home IP, this session). That file should be committed (not
  left untracked) so the design's empirical claim is reproducible and cited.

---

## 11. Owner decisions (RESOLVED — design frozen)

These four questions are now answered by the operator. Recorded here as the frozen contract.

- **Q1 — Sources at launch → DECIDED: 1b. SG MSM is primary; Google News is corroboration.**
  The direct Singapore MSM scrapers (CNA, Mothership, ST, Stomp, etc.) are the **primary**
  sources behind the `Source` interface. `GoogleNewsRSSSource` is a **corroboration / cross-check**
  partner, not the spine. This aligns with §4.4 (corroboration agent) — Google News becomes a
  corroboration signal. (Operator rationale: "the main sauce is always Singapore MSM.")
- **Q2 — Dateless candidates → DECIDED: 2b. Route to War Room for review.** Not dropped. The human
  does due-diligence (may google and find the source); the decision is recorded as training
  signal. War Room's purpose includes training the sourcing model. (See §5.1, LEARNING_LOOP §2.1.)
- **Q3 — "Cold start" → REFRAMED, not a lookback window.** See §5.3: Cold Start = the 1980–2023
  hand-built archive as a prototype for the Historical agent to enrich/discover against; Warm Start
  = 2024–Jun 2026 litmus test; Forward = daily live pipeline. The Futurist agent's first live run
  uses `FORWARD_FIRST_RUN_LOOKBACK_DAYS` (operator-set; Warm Start ≈ "since Dec 2025").
- **Q4 — Cadence → DECIDED: daily, in a cloud docker.** Failed runs, bot traps, and recommended
  after-actions are reported to War Room (FallbackLadder → DegradedRunReport, §6–7). Trigger via
  Cloud Scheduler → HTTP (§1, separate deployment task).

---

## 12. Why this resists AI drift and tech debt (design rationale)

- **One verifiable seam.** `run_ingestion_pass()` is a contract an autonomous agent (or human)
  can check against the code without understanding deployment. Drift can't hide behind infra.
- **Decoupled decisions.** Trigger, sources, dedup strategy, and filtering each evolve behind
  their own interface. Changing one doesn't force edits to the others — the opposite of the
  coupled, contradictory state the current spec is in.
- **Honest failure.** Degraded runs are loud. The system can't silently rot into "no news"
  while a source is dead — the single most dangerous failure mode for unattended autonomy.
- **Reuse over reinvention.** Terminates at the verified `war_room_queue` contract and reuses
  `check_duplicate` and the Stage 1 budget guard. New surface is minimized to exactly what's
  missing: the source abstraction and the watermark store.
  (That guard was `scrapers/groq_budget.py` when this was written. Stage 1 migrated Groq →
  Gemini in July 2026 and the file was deleted; the equivalent is now
  `filters/stage1_quota.py`, which bounds *requests* — `STAGE1_RPM` / `STAGE1_RPD` — rather
  than tokens.)
- **Named deferrals.** §9 lists what we're deliberately NOT building, so scope creep is visible
  and intentional, not accidental.
