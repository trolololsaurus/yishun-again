# Ingestion Architecture — Design Document

**Status:** Proposed for review · **Targets:** TechSpec v1.9 · **Scope:** Option B (trigger-agnostic)
**Owner decision required before build.** No code is written until this design is approved.

---

## 0. Purpose & scope

This document specifies the **forward-looking ingestion layer** for Yishun Again — the
subsystem that autonomously discovers *new* Yishun incidents on an ongoing basis and queues
them for operator review.

It does **not** cover historical backfill (done by hand + `backfill_agent.py`, now complete),
nor the deployment/trigger infrastructure (see §1, Deployment Prerequisite — flagged but
deliberately out of scope here).

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

### 3.3 First concrete adapter: `GoogleNewsRSSSource`

```python
class GoogleNewsRSSSource:
    name = 'google_news_rss'
    # Smoke-test-proven configuration:
    #  - NO after:/before: date operators (they break the RSS feed → 0 results)
    #  - query = "yishun {keyword}" per keyword in YISHUN_KEYWORDS
    #  - parse published_parsed → Candidate.published_at
    #  - resolve redirects to canonical url
    #  - polite delay between keywords; raise SourceBlockedError on 429/403/CAPTCHA markers
```

This adapter encapsulates everything learned this session. It is the only place RSS quirks live.
A future `SerpApiSource` or `BingNewsSource` implements the same `Source` protocol and drops in
with zero orchestrator changes.

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
      3. fresh = RecencyFilter(candidates, watermark, now)   # §5
      4. novel = Deduplicator(fresh)                         # §5
      5. for each novel candidate: Stage1 (budget-guarded) → Stage2 → queue row
      6. StateStore.update(source.name, max published_at seen)  # only on success
    On SourceBlockedError / SourceUnavailableError → FallbackLadder (§6).
    Always returns an IngestionReport (§7); never raises to the caller.
    """
```

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
    published_at is None                           (undateable — do not silently drop;
                                                    pass through with a 'dateless' flag so the
                                                    operator/Stage-2 can judge, OR drop with a
                                                    counted reason — see Open Question Q2)
```
First run (watermark is None): treat as "last 7 days" rather than "all of history," to avoid a
cold-start flood of the RSS grab-bag's decade-long tail. The 7-day cold-start window is a
named constant, `COLD_START_LOOKBACK_DAYS = 7`.

### 5.2 Deduplicator
Reuse the existing, verified `check_duplicate(url)` (corroboration.py) — it already checks both
`war_room_queue.source_url` and `incidents.source_urls` by canonical URL. Do not reimplement.
Within a single pass, also dedupe candidates against each other by URL (the in-run `seen_urls`
pattern that already exists).

**No fuzzy/title dedup in v1.** URL-exact only, matching current behaviour. Title/semantic
dedup is explicitly deferred (see §9) to avoid over-engineering and false merges.

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
    degraded: bool                     # True if any source was skipped
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
- **Additional source adapters** (SerpAPI, Bing, reactivating per-source MSM scrapers for the
  live path) — the interface supports them; building them is later work.
- **Auto-disabling unhealthy sources** — recorded, not acted on, to avoid silent coverage loss.
- **The `people_profiles` / entity-hub system** (spec §4.x, v1.6) — orthogonal; not touched here.
- **Pixel-art generation** — downstream of queue approval, unrelated to ingestion.

---

## 10. File layout (where the code will live)

Per the established convention (`scrapers/` = source-fetching, `classifiers/` = scoring,
`orchestrator/` = sequencing):

```
packages/agents/
├── ingestion/                      # NEW package — the Option B layer
│   ├── __init__.py
│   ├── contracts.py                # Candidate, Source protocol, errors, IngestionReport
│   ├── recency.py                  # RecencyFilter
│   ├── dedup.py                    # thin wrapper over existing check_duplicate
│   ├── state_store.py              # pipeline_state read/write
│   ├── fallback.py                 # FallbackLadder state machine
│   ├── orchestrator.py             # run_ingestion_pass() — the entrypoint
│   └── sources/
│       ├── __init__.py
│       └── google_news_rss.py      # GoogleNewsRSSSource (smoke-test-proven config)
└── (existing modules unchanged)
```

Rationale for a NEW `ingestion/` package rather than extending `scrapers/`: the live forward
pipeline is a distinct concern from historical `backfill_agent.py` and the per-source scrapers.
A clean package boundary prevents the two from entangling (the exact entanglement that made
`backfill_agent.py`'s "recent" path quietly broken).

---

## 11. Open questions for the owner (decide before build)

- **Q1 — Sources at launch.** Start with `GoogleNewsRSSSource` only (proven), or also wire one
  or two of the existing per-source RSS scrapers (CNA, Mothership) behind the new `Source`
  interface? Recommendation: **RSS-only at launch**; add MSM adapters post-launch once the
  contract is proven in production.
- **Q2 — Dateless candidates.** When `published_at is None`, route-to-review (let operator
  judge) or drop-with-count? Recommendation: **drop-with-count** for v1 (RSS almost always
  supplies a date; dateless items are usually low-quality), revisit if the counter shows we're
  losing real incidents.
- **Q3 — Cold-start window.** `COLD_START_LOOKBACK_DAYS = 7` on first run — acceptable, or
  prefer a different first-run window?
- **Q4 — Run cadence (informs, but does not block, this design).** Daily? Twice-daily? This
  sets the trigger schedule (separate task) but also the expected `fresh` volume per run.
  Recommendation: **daily** to start.

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
