# Ingestion Architecture — Design Document

**Status:** APPROVED (owner sign-off) · Q1–Q4 resolved (§11) · **Targets:** TechSpec v1.9 · **Scope:** Option B (trigger-agnostic)
**Companion:** `docs/LEARNING_LOOP.md` (how the Futurist agent improves from War Room signal).
**Owner-approved.** Code may be built against this frozen design; the Learning Loop is Phase-1 (contextual) only.

---

## 0. Purpose & scope

This document specifies the **forward-looking ingestion layer** for Yishun Again — the
subsystem that autonomously discovers *new* Yishun incidents on an ongoing basis and queues
them for operator review.

It does **not** cover historical backfill (done by hand + `backfill_agent.py`, now complete),
nor the deployment/trigger infrastructure (see §1, Deployment Prerequisite — flagged but
deliberately out of scope here).

### ⚠️ This layer SUPERSEDES the existing live pipeline (Path A — decided)

Investigation found **three** forward-pipeline orchestrators in the codebase, in different states:

| Orchestrator | Consolidation routing? | Status (before this work) |
|---|---|---|
| `orchestrator/orchestrator.py::run_graph()` (LangGraph 6-node) | ❌ none — every item becomes a fresh `pending` card; no update/skip, no incident_links | **LIVE** (wired into main.py scheduler + `/pipeline/run`) |
| `pipeline.py::run_pipeline()` | ✅ full (update/skip/new + incident_links) | **DEAD** (nothing imports or calls it) |
| `ingestion/orchestrator.py::run_ingestion_pass()` | ✅ via shared `consolidation/` module | **THIS DESIGN** (to be built) |

Consequence worth stating plainly: **the live pipeline currently has NO consolidation routing** —
in production today every candidate becomes a new `pending` card, so duplicates do not reinforce,
later reports do not enrich timelines, and recurring items are not linked into phenomena. The only
code that ever did this correctly is the dead `pipeline.py`.

**Decision (Path A): build `ingestion/` fresh; retire BOTH old orchestrators.**
- `run_ingestion_pass()` **replaces** `run_graph()` as the live forward pipeline. On cutover,
  `main.py` is repointed from `run_graph()` to the new trigger-agnostic entrypoint (part of the
  §1 deployment task), and the LangGraph graph (`orchestrator/orchestrator.py`) is **deleted**.
- `pipeline.py` is **mined then deleted**: its consolidation-routing + `write_incident_links`
  on-update logic + EDMW-aware row fields are the reference implementation for the shared
  `consolidation/` module (§5.4); once folded in, the dead file is removed.
- **End state: ONE forward pipeline.** No LangGraph graph, no orphaned `pipeline.py`, no third
  path. (Owner directive: "don't keep dead code and useless agents.")

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
   corroboration rule (§4.4) and Groq budget middleware (§4.8). Build on what is verified to
   exist, not on stale spec sections.

---

## 1. ⚠️ DEPLOYMENT PREREQUISITE (flagged debt, out of scope for this design)

**The current documented trigger mechanism does not work, and this design does not fix it —
it isolates it.**

TechSpec §11.2 deploys Cloud Run with `--min-instances 0`. TechSpec §4.1 / §4.6 rely on
**in-process APScheduler timers** "embedded in FastAPI." These are mutually incompatible:
`min-instances 0` scales the container to zero between HTTP requests, which **terminates every
in-process timer.** Under the documented deployment, no scheduled scrape has ever been able to
fire autonomously.

**This design deliberately does NOT specify the fix.** Instead it exposes a single
trigger-agnostic entrypoint, `run_ingestion_pass()` (§4), that any caller can invoke. The
recommended deployment fix — to be decided and implemented as a SEPARATE task — is:

> **Cloud Scheduler → HTTP push → a `/run/ingest` endpoint on the Cloud Run service.**
> Cloud Scheduler is a managed cron; it issues an authenticated HTTP request on a schedule;
> Cloud Run wakes, serves one ingestion pass, and scales back to zero. This is the standard
> serverless-cron pattern, costs negligibly, and resolves the `min-instances 0` contradiction.

**Why this is logged here and not designed here:** infrastructure specs rot faster than logic
specs because they change outside the codebase. Entangling the trigger with the ingestion
design would couple two independently-evolving decisions and reintroduce exactly the kind of
stale-spec drift this project already suffers from. The seam is `run_ingestion_pass()`; the
trigger lives behind it and can change without disturbing ingestion logic.

**Action owner:** deployment/infra. **Blocking for live autonomy:** yes. **Blocking for this
design or its implementation:** no — the entrypoint is callable manually (CLI) for testing and
launch.

---

## 2. Architecture overview

```
                          ┌─────────────────────────────────────────────┐
   (trigger, out of scope)│  run_ingestion_pass(sources, now)            │
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
        │  Deduplicator    │  war_room_queue + incidents (reuse check_duplicate)
        └───────┬──────────┘
                ▼
        ┌──────────────────┐  Stage 1 (Groq, budget-guarded) → Stage 2 (Claude)
        │  ExistingPipeline│  → proposed_* fields
        └───────┬──────────┘
                ▼
        ┌──────────────────┐  write rows (status='pending')
        │  war_room_queue  │  → operator reviews & publishes
        └──────────────────┘

   On any source failure → FallbackLadder (§6) → DegradedRunReport → War Room alert
   On success → StateStore.update_watermark(source, max_seen_published_at)
```

Every box is a single-responsibility unit with an explicit contract. The orchestrator owns
sequencing and failure handling; it owns no fetching or parsing logic itself.

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
    url: str                    # CANONICAL article url (redirects resolved)
    source_name: str            # human-readable, e.g. "Channel NewsAsia"
    source_type: str            # 'msm' | 'reddit' | 'edmw' | 'rss' ...
    published_at: date | None   # parsed publication date; None if unknowable
    discovered_via: str         # which Source produced this (e.g. 'google_news_rss')
```

**Contract rules:**
- `url` MUST be the canonical article URL, not a wrapper (e.g. resolve `news.google.com`
  redirects). Dedupe correctness depends on this.
- `published_at` MUST be parsed from the source's own date field, never inferred from "now."
  If a source cannot supply a date, `published_at = None` and the item is treated
  conservatively (see §5 RecencyFilter).
- `Candidate` is `frozen` (immutable). Transformations produce new objects.

### 3.2 `Source` (the pluggable interface)

```python
class Source(Protocol):
    name: str                   # stable id, e.g. 'google_news_rss'; key into pipeline_state
    enabled: bool

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

**Why `since` is advisory, not authoritative:** the Google News RSS smoke test proved the feed
returns a relevance-ranked grab-bag spanning years, ignoring date operators. A source cannot be
trusted to honour `since`. The orchestrator therefore always re-filters by watermark (§5). This
is defence-in-depth: the source may optimise with `since`, but correctness lives in the
orchestrator.

### 3.3 Concrete adapters: MSM primary, Google News corroboration (Q1 = 1b)

Per the owner decision (§11, Q1 = 1b), **Singapore MSM is the primary source spine; Google News
RSS is a corroboration / cross-check partner.** Both implement the same `Source` protocol.

**Primary — the SG MSM adapters.** The existing per-source scrapers (`scrape_cna.py`,
`scrape_mothership.py`, `scrape_straitstimes.py`, `scrape_stomp.py`, etc. — see TechSpec §4.0b)
are wired behind the `Source` interface as the primary candidate producers. Each becomes a
`Source` implementation emitting `Candidate` objects. (RSS-first where the outlet has a feed; HTML
fallback where it doesn't, per §4.1 implementation notes.)

**Corroboration — `GoogleNewsRSSSource`.** Smoke-test-proven config: **no `after:`/`before:` date
operators** (they break the RSS feed → 0 results), one `yishun {keyword}` query per keyword, parse
`published_parsed`, resolve redirects, raise `SourceBlockedError` on 429/403/CAPTCHA markers. Its
role is to **cross-check and catch what the direct MSM scrapers miss or delay** — feeding the
corroboration count (§4.4), not standing as the primary spine.

All adapters are interchangeable behind the protocol; adding/removing one is a config change, not
an orchestrator change. A future `SerpApiSource` / `BingNewsSource` drops in identically.

> Implementation sequencing note: it is reasonable to bring the MSM adapters online incrementally
> (start with the most reliable RSS feeds — CNA, Mothership — then add the HTML-scraped outlets),
> but the architecture treats MSM as primary from day one per the owner decision. Do not invert
> this to "Google News primary" — that was an earlier, rejected framing.

---

## 4. The entrypoint (the drift-resistant seam)

```python
def run_ingestion_pass(
    sources: list[Source],
    now: datetime,
    *,
    dry_run: bool = False,
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
           a. Stage1 (budget-guarded) -> Stage2 -> draft
           b. result = consolidation.check(candidate, draft) # §5.4 (shared module)
           c. row = build_queue_row(candidate, draft, result) # §5.4 (shared builder)
           d. write row to war_room_queue (new / update / phenomenon_member)
           e. check_milestones() — NON-FATAL herald/milestone check after the
              queue insert (preserves milestone detection from the retired
              LangGraph graph; try/except so it never disrupts the pass)
      6. StateStore.update(source.name, max published_at seen)  # only on success
    On SourceBlockedError / SourceUnavailableError -> FallbackLadder (§6).
    On infrastructure failure (DB unreachable during dedup/queue-write) ->
      abort pass as DEGRADED with infra_error (§5.2, §7).
    Always returns an IngestionReport (§7); never raises to the caller.
    """
```

> **Built-reality notes (post-implementation reconciliation):**
> - **Herald/milestone (step 5e)** was NOT in the original design but IS required: the retired
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
- `dry_run=True` runs the full fetch/filter/dedupe path and writes NOTHING (no queue rows, no
  watermark advance). For testing and the smoke-test lineage.
- Watermark advances **only** for sources that completed successfully. A blocked source keeps
  its old watermark so the next run retries the same window — no data is skipped due to a block.

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
First run (watermark is None): see §5.3 — the cold-start concept is NOT a "last N days" lookback.
The operator's model is a three-phase one (Historical/Cold+Warm, then Futurist/Forward); the
Futurist agent's first live run uses a small forward window, but "Cold Start" as a whole means
something different. Read §5.3 before implementing first-run behaviour.

### 5.2 Deduplicator
Reuse the existing, verified `check_duplicate(url)` (corroboration.py) — it already checks both
`war_room_queue.source_url` and `incidents.source_urls` by canonical URL. Do not reimplement.
Within a single pass, also dedupe candidates against each other by URL (the in-run `seen_urls`
pattern that already exists).

**No fuzzy/title dedup in v1.** URL-exact only, matching current behaviour. Title/semantic
dedup is explicitly deferred (see §9) to avoid over-engineering and false merges.

> **Infra-failure handling (review S1).** `check_duplicate()` currently fails OPEN (returns
> `False`) on a Supabase error — i.e. treats an item as novel when it cannot verify. That is fine
> for a single lookup but dangerous at pass scale: during a Supabase outage, *every* item looks
> novel and the subsequent queue-writes also fail. **Rule:** the orchestrator MUST distinguish
> source-fetch failures (handled per-source by the FallbackLadder, §6) from *infrastructure*
> failures (DB unreachable during dedup or queue-write). On a detected infrastructure failure the
> **entire pass aborts as DEGRADED** — it does not continue treating everything as novel. The
> `IngestionReport` records `infra_error` distinctly from source skips.

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
  doc. SG MSM primary, Google News corroboration (Q1 = 1b). This is the **Futurist agent**, the
  subject of the Learning Loop.

The `run_ingestion_pass()` machinery in this document is the **Forward / Futurist** pipeline. The
Historical agent (Cold + Warm enrichment/discovery) is a **distinct agent** closer in character to
the manual consolidation work — it reads the archive and proposes enrichments/links for human
review, rather than running the live scrape pipeline. It is specified separately (TechSpec §4.x /
a future `HISTORICAL_AGENT_DESIGN.md`); it is NOT a mode of `run_ingestion_pass()`.

For the Futurist agent's genuine first live run only, use a small forward window constant
`FORWARD_FIRST_RUN_LOOKBACK_DAYS` (operator to set; the Warm Start litmus test effectively sets
this to "since Dec 2025"). This is a narrow implementation detail, not the meaning of "cold
start."

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

**Routing the result into a queue row (the single shared builder):**

```python
def build_queue_row(candidate: Candidate, draft: Stage2Draft,
                    result: ConsolidationResult,
                    *, edmw_signal_count: int = 0) -> dict:
    # ONE correct builder, handling all three cases. Replaces the inline
    # _build_queue_row in backfill_agent.py (already extracted) AND the dead
    # pipeline.py::_build_queue_row (mined then deleted). Candidate->dict
    # conversion (dataclasses.asdict) happens HERE, at the boundary
    # (fixes review S3 — the frozen Candidate dataclass must not be **spread directly).
```

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

- **`kind='new'`** → standard pending queue row (`status='pending'`), no `update_target_incident_id`.
- **`kind='update'`** → queue row with `status='update'` and `update_target_incident_id` set, so
  the operator approves an **append to the matched card's `source_timeline`** (requirement 3:
  enrich the timeline, do not just tag a link). `proposed_role` carries the timeline role.
- **`kind='phenomenon_member'`** → queue row that proposes an `incident_links` row to the
  `phenomenon_hub_id` AND (where the source warrants its own card) a new individual card, per the
  CONSOLIDATION_RULES.md pattern-linking rules (umbrella hub + individually-sourced members;
  requirement 4). The `agent_reason` records the distinction (defamation guard, rulebook rule 11).
- **`corroborates=True`** on any kind → increments the matched incident's corroboration signal
  rather than creating a parallel entry (requirement 2: reinforce, don't duplicate).

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
NORMAL ──fetch ok──────────────────────────────▶ done (advance watermark)
   │
   ├─ SourceUnavailableError (transient) ──▶ BACKOFF
   │                                          wait one fixed interval, retry ONCE
   │                                          ├─ ok ─▶ done
   │                                          └─ fail ─▶ SKIP_SOURCE (degraded)
   │
   └─ SourceBlockedError (bot trap) ───────▶ SKIP_SOURCE immediately
                                              (do NOT retry into a ban; matches smoke-test policy)

SKIP_SOURCE: record reason, leave watermark UNCHANGED, continue to next source.
```

**After all sources processed:** if ANY source ended in SKIP_SOURCE, the pass is **DEGRADED**.
A degraded pass:
- still queues whatever the healthy sources produced (partial success is fine),
- emits a `DegradedRunReport` into a War Room operator notification ("Source X was blocked /
  unavailable this run; its watermark was not advanced; it will retry next run"),
- is recorded in `pipeline_state` run history (§8).

**Critical invariant:** a blocked source NEVER advances its watermark, so no window is ever
skipped because of a block. The next run re-attempts the same `since`.

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
    per_source: list[SourceResult]     # name, status, fetched, fresh, novel, queued, reason
    total_queued: int
    new_count: int                     # kind='new' rows
    update_count: int                  # kind='update' rows (timeline enrichments proposed)
    phenomenon_count: int              # kind='phenomenon_member' rows
    degraded: bool                     # True if any source was skipped
    infra_error: str | None            # set if the pass aborted on a DB/infrastructure failure
    notes: list[str]
```

A pass that queues zero items is only "healthy-quiet" if every source was NORMAL and simply had
no new items. A pass that queues zero because a source was blocked is DEGRADED and says so. The
distinction is the whole point of goal #2.

---

## 8. New schema: `pipeline_state` table

The spec has **no** watermark/state mechanism (confirmed: no `pipeline_state`, `watermark`,
`last_run`, or `last_seen` anywhere). This is the one genuinely new table Option B requires.

```sql
CREATE TABLE pipeline_state (
  source_name        TEXT PRIMARY KEY,          -- matches Source.name, e.g. 'google_news_rss'
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

`consecutive_failures` enables a future health policy (e.g. alert louder after N failures) but
v1 only records it; no auto-disable (that would be silent failure of a different kind).

---

## 9. Explicitly NOT in this design (anti-scope-creep)

To keep Option B reviewable and drift-resistant, the following are **named and deferred**:
- **The trigger infrastructure** (Cloud Scheduler/IAM/HTTP endpoint) — §1, separate task.
- **Fuzzy / semantic dedup** — URL-exact only in v1.
- **Additional source adapters beyond launch set** (SerpAPI, Bing) — the interface supports them.
- **Auto-disabling unhealthy sources** — recorded, not acted on, to avoid silent coverage loss.
- **The `people_profiles` / entity-hub system** (spec §4.x, v1.6) — orthogonal; not touched here.
- **Pixel-art generation** — downstream of queue approval, unrelated to ingestion.
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

> **GroqBudget seeding (review S2).** `groq_budget.py` is currently per-instance, in-memory, with
> no cross-invocation persistence — a true tokens-per-DAY limit only holds if the budget is seeded
> from a persisted daily counter. `ingestion/budget.py` wraps `groq_budget.py` to **seed from and
> persist a per-day counter** (keyed by SGT date), so a daily run plus any manual same-day re-run
> share one budget and cannot together blow the 500k TPD ceiling. (Q4 cadence = daily makes the
> single-run case correct already; this hardening covers same-day re-runs without hardcoding the
> assumption.)

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
  `check_duplicate` and `groq_budget`. New surface is minimized to exactly what's missing: the
  source abstraction and the watermark store.
- **Named deferrals.** §9 lists what we're deliberately NOT building, so scope creep is visible
  and intentional, not accidental.
```
