# QA Backlog — Full-Codebase Functional Review (June 2026)

Companion to `POST_LAUNCH_HARDENING.md`. Records the 39 issues surfaced by the
June-2026 full-codebase QA sweep (public web, War Room CMS, agents pipeline, DB/
security) with a tech-lead-approved fix for each, ranked Critical → Low.

**Status:** 🔴 Open · 🟡 Partly fixed / residual remains · 🟢 Cleanup script pending ·
✅ Done · ⛔ Moot (the code it describes no longer exists) · 🔵 Won't fix

> **Implementation status — re-verified against the working tree 2026-08-02.**
> **Closed in code:** C1, C2, C3, C4 · H1, H2, H3, H4, H5, H6 · M1, M2, M3, M4,
> M5, M6, M7, M9, M10, M11, M13, M14 · L1, L2, L5, L10, L11 · A1–A5, A10, A11,
> A13. Plus migrations **010** (C3/C4/M7) and **013** (L10), and the standalone
> guard scripts (`test_stage2_guardrails.py`, `test_political_alert.py`,
> `test_source_allowlist.py`, `test_watermark_advance.py` — 30 `test_*.py` in
> `packages/agents/`, run directly, not under pytest).
> **Moot — the code they describe no longer exists:** M12 and A2, both about
> `ingestion/sources/google_news_rss.py`, which was **deleted on 2026-08-02**.
> The history is kept below because the lesson outlived the file.
> **Operator/live-DB, not answerable from the tree:** H7, H8
> (`cleanup_corrupted.py --apply`).
> **Still open:** M8 (VariableSizeList), M15 (migration runner), M16 (partial),
> L3, L4 (won't-fix: "Deaths: 0" conveys confirmed-none), L6, L7, L8, L9 ·
> A6, A7, A8, A9, and A12's cost-accounting residual.

---

## 🔴 Critical

### C1 — Political-content guardrail unenforced ✅ (verified 2026-07-30)
**Was:** guardrail only logged; the final `confidence` was overwritten by Haiku's value.
**Closed in `filters/stage2_writer.py::_classify`** — `political: true` forces
`confidence = 0.0` **before** the merge, and `write_stage2` prepends the
`[POLITICAL CONTENT DETECTED — REJECT]` marker.
**Extended 2026-07-30:** it now also fails *loudly* — a distinct
`raw_content._political_flagged` marker, an operator notification through the
existing dedup ledger, and a `warning`-level `agent_events` row. A silently
zeroed row was indistinguishable from any other low-confidence row, so political
stories were dropped with no trace. The guardrail itself is unchanged.
**Extended again 2026-08-02:** the guardrail is now read **first**, before any
field validation. It used to sit below the classification/severity/confidence
coercion, and `result["classification"].lower()` raised `AttributeError` on
`"classification": null` — which is what the model tends to return on a political
story, because it is being told to reject rather than categorise. The guardrail
was therefore unreachable for a subset of the very content it exists to catch:
the candidate died on an exception, so confidence was never forced to 0, the
marker was never prepended, and neither the operator email nor the `agent_events`
row fired. A non-political row with a bad classification still raises — that is a
genuine model failure and must not be swallowed.
**Guards:** `test_stage2_guardrails.py` (now also covers `political: true` with
`classification: null` and with an invalid classification string),
`test_political_alert.py` (asserts no env switch can disable it and nothing in
the alert path writes back to `confidence`).

### C2 — EDMW URL leaks into `source_urls` ✅ (verified 2026-07-30)
**Closed in `ingestion/orchestrator.py`** — a signal candidate gets
`source_urls=[]` and carries only `edmw_signal_count`. The test is
`is_signal_source(candidate.source_type, candidate.url)`, **never** a bare
`== 'edmw'`: `scrape_edmw` and `scrape_reddit` both emit the canonical
`'signal'`, and that vocabulary mismatch breached the guardrail once already
(`92d6305`). Also enforced downstream in `build_queue_row` via
`check_source_urls`, and again in `auto_publish.check_eligibility`.
**Guard:** `test_source_allowlist.py`.

### C3 — UTM analytics silently fail (RLS vs anon key) ✅
**Was:** `utm_events` had RLS on with no INSERT policy, and `apps/web/lib/supabase.ts`
writes with the publishable key → every UTM POST was rejected (500).
**Closed by `migrations/010_qa_hardening.sql`** — the second of the two options was
taken: a tightly-scoped `anon_insert_utm_events` policy, `FOR INSERT TO anon WITH
CHECK (true)`, and **no SELECT** (anon still cannot read the table). No admin client
was introduced, so the secret key stays out of the public app entirely. Acceptable
because `utm_events` stores no PII — hashed UA + Cloudflare geo only — and
`/api/utm/log` is rate-limited to 30 req/min per IP.

### C4 — `source_urls ≥ 1` constraint broken for empty arrays ✅ (verified 2026-07-30)
**Closed by `migrations/010_qa_hardening.sql`** —
`CHECK (cardinality(source_urls) >= 1)` replaces the old
`array_length(source_urls, 1) >= 1`, which returned NULL for `'{}'` and so let an
empty array pass. Confirmed present in the migration file; the 2026-07-30
baseline sweep found **0 published incidents with an empty `source_urls`**.

---

## 🟠 High

### H1 — `confirm-close` never writes the incident ✅
**Was:** the operator-confirmed conclusion was not persisted — only the notification
was dismissed, leaving `is_developing=TRUE` with no `conclusion_type`/`concluded_at`.
**Closed in `war-room/app/api/queue/[id]/confirm-close/route.ts`** — before dismissing,
it writes `{ is_developing:false, conclusion_type:'timeout', concluded_at, latest_source_role:'timeout' }`
keyed on `rc.incident_id`, and a failed update is a hard 500 rather than a swallowed
error. `rc.incident_id` is run through `validateUUID` first: `raw_content` is
pipeline-written JSONB, and a malformed value otherwise reached PostgREST and 500'd
with a raw DB error. Note `concluded_at` stamps the confirmation time, not a source
date — this is a timeout conclusion, so there is no source event to date it from.

### H2 — `approve` swallows 3 post-insert errors ✅
**Closed in `war-room/app/api/queue/[id]/approve/route.ts`** — each `{error}` is
captured. The queue-status update governs idempotency, so its failure is a hard 500
carrying the `incident_id` and a "do not re-approve" message; the `incident_links`
and `training_signals` failures are logged and non-fatal (a 23505 on a link is
expected and stays quiet). The status update is also a CAS
(`.eq('status','pending').select('id')`): if a concurrent approve/reject already
claimed the item, 0 rows match and the just-inserted incident is deleted, so the
two-operator race can no longer double-publish. Same treatment applied to
`backfill-bulk` (M11).

### H3 — Dateless items stamped with *today* on approve ✅
**Closed in `approve/route.ts`** — the date is taken from the operator's input, else
`raw_content.date`/`incident_date`; if none parses it returns **422** with a message
telling the operator to set `incident_date` before approving. Validation is a
full-match `^\d{4}-\d{2}-\d{2}$` plus `Date.parse`, because the old prefix regex let
`2026-99-99` through to Postgres, which bounced it as a raw DB error. `backfill-bulk`
carries the same block per item. This is why a source must supply `published_at` to
be registered at all — see `ingestion/sources.get_enabled_sources()`.

### H4 — Live rows mislabeled `_backfill:true` ✅
**Closed in `consolidation/queue_row.py`** — `build_queue_row` takes
`is_backfill: bool = True` (the default keeps the backfill agent's behaviour) and
writes it to `raw_content._backfill`. `ingestion/orchestrator.py` passes
`is_backfill=False`, so forward-pipeline drafts no longer land in the bulk-approve
bucket.

### H5 — Home map shows all-time pins on load, then shrinks to current year ✅
**Closed in `apps/web/app/page.tsx`** — the SSR map query carries the same half-open
`gte('incident_date', '${currentYear}-01-01') / lt(…, '${currentYear+1}-01-01')`
filter as `/api/map`, so the initial pins match the default year and the sidebar.

### H6 — `confirm-update` date corruption ✅
**Closed in `confirm-update/route.ts`** — the timeline entry and `incident_date` use
the candidate's real article date (`rc.date` → `rc.published_at` → the incident's
existing date → `first_reported_at`), never `new Date()`. `incident_date` clamps to
the later of the existing and new dates and `first_reported_at` to the earlier, so a
merge can never push the date into the future. The route also now claims the queue
item **before** mutating the incident and releases the claim if the mutation fails —
the old order left a raced item re-confirmable, appending duplicate timeline entries
and double-counting `update_count`.

### H7 — 8 incidents corrupted (`incident_date=2026-06-23`) 🟢
From the War Room merges. `cleanup_corrupted.py` is present in `packages/agents/`.
**Status:** whether `--apply` has been run is a live-DB question the working tree
cannot answer — verify against Supabase before assuming either way.

### H8 — `corroboration_count` stale on 13 incidents 🟢
Merges didn't bump the count → lightning under-counts. Same script, same caveat as H7.

---

## 🟡 Medium

- **M1 — Dead "Sev ≥ N" filter** ✅ `/api/incidents` reads a `min_severity` param
  (clamped to 1–5, anything else ignored) and applies `.gte('severity', n)`;
  `timeline/TimelineClient.tsx` sends it from `buildUrl` whenever `minSev > 1`.
- **M2 — Chaos counts polluted by `custom` class** ✅ Both count sites — `app/api/chaos/route.ts`
  and the SSR homepage `app/page.tsx` — only increment `acc[cls]`/`acc.total` when
  `cls ∈ {heart,clown,dagger}`, so a `custom` row can't add a phantom key or stop the
  chips summing to ALL.
- **M3 — `computeChaosScore` NaN on null severity** ✅ `apps/web/lib/utils.ts` —
  `sum + (inc.severity ?? 0) * weight`. (The same function was later rebalanced to the
  exponential curve; the null guard is unchanged.)
- **M4 — `/api/map` Dec-31 boundary drops year-end pins** ✅ `app/api/map/route.ts` uses the
  half-open `.gte(…'${year}-01-01').lt(…'${year+1}-01-01')` pair, matching feed and chaos.
- **M5 — `_classify` int() crash on non-numeric deaths/injuries** ✅ `filters/stage2_writer.py` —
  `max(0, int(val))` sits in `try/except (TypeError, ValueError)` and falls back to `None`.
- **M6 — `/api/map` has no rate limiting** ✅ `app/api/map/route.ts` opens with
  `rateLimit(getIp(req))` → 429, like every other `/api/*` route.
- **M7 — `war_room_queue` incident FKs have no `ON DELETE`** ✅ `migrations/010_qa_hardening.sql`
  recreates both FKs (`incident_id`, `update_target_incident_id`) as `ON DELETE SET NULL`,
  mirroring `utm_events.incident_id` and preserving queue history.
- **M8 — Fixed `ITEM_HEIGHT=152` clips tall cards** (`components/IncidentFeed.tsx:8`). 🔴
  Still a `FixedSizeList` with `ITEM_HEIGHT = 152`, and `IncidentCard` has no max-height clamp.
  **Fix:** move to `VariableSizeList` with measured rows, or enforce a hard max-height clamp on the card.
- **M9 — `confirm-update` links missing `confirmed_by_operator` + swallowed error** ✅
  `confirm-update/route.ts` inserts `incident_links` with `confirmed_by_operator: true`
  (the operator just confirmed the update) and logs any error whose `code !== '23505'`.
- **M10 — `unpublish` has no `is_published` precondition** ✅
  `incidents/[id]/unpublish/route.ts` returns `{ok:true, noop:true}` for an already-draft
  incident, and the update itself is a CAS (`.eq('is_published', true).select('id')`) —
  so of two concurrent unpublishes only the one that actually matched a row writes the
  training signal.
- **M11 — `backfill-bulk` misreports `updated` + swallows approve errors** ✅
  `backfill-bulk/route.ts` reports `rejectedIds.size` from the CAS's `.select('id')`
  rather than the fetched count (which over-reported when a concurrent request got there
  first), and pushes every approve-path failure — missing title/summary, no valid date,
  insert error, lost race, queue-update failure — into `errors`.
- **M12 — Candidate.url canonicalization violated on resolver failure** ⛔ **Moot as of 2026-08-02.**
  The file this describes, `ingestion/sources/google_news_rss.py`, was **deleted**: the
  `news.google.com/rss/articles/<blob>` wrappers do not HTTP-redirect (decoding them needs
  a reverse-engineered `batchexecute` RPC that Google rotates), so resolution failure was
  the normal case, not the edge case. Two live rows on 2026-08-01 showed all three
  consequences at once — dedup missed them (it matches on URL), a redirect sat where a
  citation belongs in `war_room_queue.source_url` and `source_urls`, and
  `unapproved_source_domain` tripped. Rather than canonicalize on the blob, the aggregator
  was removed and replaced by discovery adapters that emit the **publisher's own URL**:
  `ingestion/sources/news_sitemap.py` (9 outlets' Google-News sitemaps) and
  `ingestion/sources/wp_search.py` (2 WordPress `?s=yishun&feed=rss2` feeds). A third
  allowlist rule now backstops the class of bug:
  `classifiers/source_allowlist.REDIRECT_DOMAINS` + `is_redirect_domain()`, checked
  **first** in `classify()` and without consulting the `sources` table, so it cannot be
  defeated by adding the host to `sources`; `check_source_urls()` reports them under
  `dropped_redirect`, and `consolidation/queue_row.py` substitutes a real publisher URL
  for a redirector `source_url`. `scrapers/_gnews_helpers.py` survives — the backfill
  agent and the cleanup scripts still import `_resolve_redirect` — and
  `test_gnews_resolve.py` still covers its decoder.
- **M13 — Queue-dedup asymmetry (`proposed_summary` vs `summary`)** ✅
  `consolidation/check.py` falls back to `raw_content.summary` when `proposed_summary`
  is empty.
- **M14 — `source_type` vocab drift (`edmw`/`rss` vs `signal`)** ✅ Standardised at the
  adapter boundary rather than in the schema: `classifiers/source_allowlist.canonical_source_type()`
  collapses any signal spelling to `'signal'`, so no `Candidate` carries the legacy
  `'edmw'` downstream (`ingestion/contracts.py` documents the canonical vocabulary on the
  field). Note this is a normalisation, not a CHECK — guardrail #2 does **not** rest on
  it: `is_signal_source()` is deliberately belt-and-braces, testing both vocabularies
  *and* resolving the URL's domain against the `sources` table.
- **M15 — No migration runner / 006 saga** (`migrations/`). 🔴 **Still open.** Migrations
  are hand-applied in the Supabase SQL Editor, in order, with nothing recording what has
  run. The tree is now at **015** (`014_image_status.sql`, `015_image_status_check.sql`),
  so the window for a mis-ordered or skipped apply has only widened — and there are now two
  demonstrated failure modes for a skipped file, neither of which announces itself. **009**
  and **011** extend `training_signals.action` CHECKs; without them the matching inserts are
  *silently rejected* and the learning loop records nothing, which reads as quiet rather
  than as an error. **013** drops the `USING(true)` policies of L10; until it is applied the
  anon key keeps full read/write on `pattern_alerts` and `people_profiles` — a security hole
  that stays open for exactly as long as nobody notices the file was skipped.
  **Fix:** adopt Supabase CLI `db push` + a schema-version ledger. The superseded 006 was
  **renamed, not deleted** — `006_SUPERSEDED_DO_NOT_RUN_ingestion_learning_loop_schema.sql`
  — which removes the foot-gun without losing the history; a real runner would make the
  rename unnecessary.
- **M16 — Map year `useEffect` races `load`; filter not re-applied on year change**
  (`components/IncidentMap.tsx`). 🟡 **Half closed.** The `load` race is handled: the
  year effect applies the new FeatureCollection immediately if the `incidents` source
  exists, else defers via `map.once('load', apply)`, and cancels on unmount. The
  `didMount` skip was not added, so the first run re-fetches the year SSR already
  rendered — harmless since H5 made those the same year, but a wasted request.
  Re-applying `activeFilter` after `setData` proved unnecessary: `setFilter` is a layer
  property and survives a source-data swap.

---

## 🔵 Low

- **L1 — `validateUUID` rejects uppercase UUIDs** ✅ `war-room/lib/utils.ts` — the regex
  carries the `/i` flag; uppercase/mixed-case UUIDs are valid per RFC 4122.
- **L2 — `UTMLogger` ships `console.log`s (incl. response body) to prod** ✅
  `components/UTMLogger.tsx` is fire-and-forget — `.catch(() => {})`, no response-body
  logging on any path, so no `NODE_ENV` gate was needed.
- **L3 — Timeline year input not clamped → silently returns unfiltered archive**
  (`app/timeline/TimelineClient.tsx`). 🔴 The input now carries `min={2020} max={2100}`,
  but it is not inside a form, so the browser never enforces them on a typed value. A
  3-digit or out-of-range entry still fails `sanitiseYear`'s `^\d{4}$`, which returns
  `null` — i.e. the whole archive, unfiltered, with no indication the year was ignored.
  **Fix:** validate client-side or surface "invalid year".
- **L4 — Detail page shows "Deaths: 0" when `deaths=0, injuries>0`** (`app/incidents/[slug]/page.tsx`).
  🔵 **Won't fix** — `deaths: 0` means *confirmed none*, which is exactly the distinction the
  Stage 2 extraction rules preserve (null = not mentioned, 0 = confirmed none). The block is
  gated on `(deaths ?? 0) > 0 || (injuries ?? 0) > 0`, so it never renders on a story with
  neither; showing the confirmed zero alongside an injury count is the intended reading.
- **L5 — tz drift: `new Date('YYYY-MM-DD').getFullYear()` parses UTC** ✅ `app/page.tsx`
  builds the year set with `parseInt(String(r.incident_date).slice(0, 4), 10)`; a Jan-1
  SGT date no longer rolls back into the prior year.
- **L6 — `/api/incidents` ignores `limit` param clients send** (`app/api/incidents/route.ts`,
  `components/IncidentFeed.tsx:52`). 🔴 Unchanged and now purely cosmetic: `IncidentFeed`
  still sends `limit`, the route still fixes the page at `PAGE_SIZE = 20`, and both
  constants are 20 — so the two agree by coincidence rather than by contract.
  **Fix:** honour it (clamped) or drop it from the client.
- **L7 — Circuit breaker resets on Stage-1 reject + only counts rate-limit/billing**
  (`ingestion/orchestrator.py`). 🔴 Both halves still stand. `consecutive.clear()` runs on
  the Stage-1 reject path — the highest-volume branch by far, at 60–70% of raw volume — so
  a systemic failure interleaved with normal rejects never accumulates. `_classify_error`
  still returns only `rate_limit_429` and `anthropic_billing`; 5xx, timeout and connection
  errors classify as `None` and are not counted.
- **L8 — `record_run` outside try/except can violate "never raises"**
  (`ingestion/orchestrator.py`, in `run_ingestion_pass`'s tail). 🔴 Still outside the
  `try`, and `state_store.record_run` does not guard its own `.execute()`, so a
  `pipeline_run_history` insert failure propagates out of the pass *after* all the real
  work committed. **Fix:** wrap in `try/except` + log. Worth doing precisely because this
  table is what `ops/supervisor.py` derives outage alerting from.
- **L9 — In-memory rate limiter is per-instance** (`apps/web/lib/rateLimit.ts`). 🔴 (self-documented
  in the file header). The map is now bounded (`MAX_TRACKED_IPS = 10_000`, stale sweep then
  clear) so spoofed IP headers can't grow memory without limit — but the window is still
  per-instance, and the effective global limit is still N-instances × limit.
  **Fix:** move to Upstash/Redis before relying on it.
- **L10 — `pattern_alerts`/`people_profiles` use `USING(true)` policies** ✅ Closed by
  `migrations/013_rls_fix_and_reddit_seed_cleanup.sql`, which drops
  `operator_full_access` on `pattern_alerts` and `operator_only` on `people_profiles`.
  Default-deny was taken over scoping `TO service_role`: RLS stays enabled with no policy,
  so the tables are service-role only like every other private table. Both are covered by
  `tools/rls_audit.py`.
- **L11 — `incident_links` query relies on RLS for `confirmed_by_operator` without asserting it** ✅
  `app/incidents/[slug]/page.tsx` filters explicitly with `.eq('confirmed_by_operator', true)`
  rather than trusting the policy.

---

## Addendum — July 2026 autonomy sweep

Found while wiring the daily unattended pass (`docs/AUTONOMY.md`). Opened with five
(A1–A5) and grew as the autonomy layer landed; every one was a live defect, not a
hypothetical.

### A1 — `Stage1RpmThrottle.wait_if_needed` could loop forever ✅
`filters/stage1_quota.py`. With `STAGE1_RPM=0`, `len(window) < self._rpm` is never
true, so the throttle slept in 0.1 s steps indefinitely — a genuine infinite loop
reachable from an env-var typo, on the hot path of every Stage 1 call.
**Fixed:** RPM floored at 1; `MAX_WAIT_SECONDS = 90` cap per call. A 429 is
recoverable, a hang is not.

### A2 — Google News consumed the entire pass budget ✅ (source since removed — 2026-08-02)
`ingestion/sources/google_news_rss.py`. `_resolve_redirect` (one HTTP round-trip
each) ran on all ~650 feed entries *before* `RecencyFilter` discarded ~600 of
them as stale. A dry run took **909 s** and hit its deadline inside this source,
so `reddit` and `edmw` never ran at all — the two sources ordered behind it were
being silently starved on every pass.
**Fixed at the time:** filter on the RSS entry's own `pubDate` before resolving;
dedup on the wrapper URL first; `MAX_RESOLVES_PER_FETCH = 120` for cold starts.
**909 s → 59 s**, the remainder being the mandatory politeness delay. Dateless
entries were still never dropped.
⛔ **Both the source and those mitigations are gone.** `google_news_rss` was
deleted on 2026-08-02 for the correctness reason in M12 (unresolvable wrappers
stored as `Candidate.url`), not for cost — so `MAX_RESOLVES_PER_FETCH` no longer
exists anywhere in the codebase. **The lesson outlived the file and is now
load-bearing elsewhere:** apply the cheap filter *before* the expensive fetch.
`ingestion/sources/news_sitemap.py` says so in its own header, and it is why the
sitemap adapters date-filter entries before touching an article. Do not
reintroduce an aggregator here.

### A3 — The pass deadline could not stop a pass ✅
`ingestion/orchestrator.py`. `max_duration_seconds` was checked in exactly one
place: inside the candidate loop, *after* dedup. So (a) a pass could not abort
between sources and kept starting new fetches long after its budget was gone, and
(b) a pass where every candidate was a duplicate `continue`d past the check and
never evaluated it at all.
**Fixed:** checked before each source's fetch and before dedup. `fallback`'s 30 s
retry backoff is now skipped when the deadline is near (15 sources × 30 s = 7.5 min
of sleep that was entirely untracked against the budget).

### A4 — `source_reputation` had no writer — the learning loop was open ✅
`ingestion/learning.py` reads the table every pass and nudges confidence ±0.10 from
`trust_score`. **Nothing in the repository ever wrote it.** Every domain resolved to
the 0.500 default, both thresholds (0.700 / 0.300) were unreachable, and the nudge
was a permanent no-op. The loop drawn closed in `LEARNING_LOOP.md` §5 was, in code,
a line ending in an empty table.
**Fixed:** `ops/learning_monitor.rebuild_source_reputation()` recomputes trust daily
from operator verdicts (Laplace-smoothed ratio, agent decisions excluded).

### A5 — `_job_jom` scheduled a scraper that does not exist ✅
`main.py`. `scrape_jom.py` was deleted back in v1.4, but the job stayed registered
on a 360-minute interval, throwing `ModuleNotFoundError` on every fire and logging
it as a scraper failure — permanent noise in exactly the log an operator would scan
for real breakage. The `Jom` seed row also still sat in `sources`.
**Fixed:** job removed, remaining `jom` references cleared from `MSM_DOMAINS` lists
and `scrape_discovery`, seed row deleted in migration 011.

### A10 — `source_reputation` still cannot populate: `training_signals.source_url` is never written ✅
Found immediately after 011 landed. `rebuild_source_reputation()` (A4's fix) reads
`training_signals.source_url` — and **all 130 rows had it NULL**, so the rebuild scored
zero domains. The War Room routes that insert training signals wrote
`incident_id`/`action`/`decision`/`operator_changes` but never `source_url` or `queue_id`
(those columns arrived with migration 006 for the ingestion-era schema and no route was
updated). So A4 fixed the writer, but the writer had no input: the loop was still open.
**Fixed:** all four source-quality routes now populate `queue_id`, `source_url`,
`source_name`, `source_type` and `proposed_classification`/`proposed_severity` —
`queue/[id]/approve`, `queue/[id]/reject`, `queue/[id]/confirm-update` and
`backfill-bulk` (both its reject and approve paths). The inserts are `await`ed:
in a serverless function an unawaited insert can be frozen mid-flight when the
response returns, which starves the loop just as effectively as a NULL column.
Rows written before this landed still carry NULL and are unrecoverable, so the
rebuild's domain coverage only starts from here.
**Related guard shipped:** `MIN_DOMAIN_OBSERVATIONS = 10` — trust stays at the neutral
0.500 until a domain has ten verdicts. Without it, 3 approvals and 0 rejections scores
0.800, clears the `TRUST_BOOST_THRESHOLD`, and buys a +0.10 confidence nudge that can
push a 0.86 draft over the 0.95 auto-publish gate. Rejections are structurally scarcer
than approvals (a rejected draft has no `incident_id` to trace a domain through), so
early data skews positive by construction.

### A11 — bulk approvals inflate the agreement rate ✅
`backfill-bulk` approves many cards in one click, writing one `training_signals` row per
card with no `operator_changes` — indistinguishable from a considered per-item approval.
`learning_snapshots` therefore counts them as "operator agreed, unchanged." The first real
snapshot read `learning` at +36.8pp (0.414 → 0.782, n=101), with `dagger` at **100%
agreement over 53 samples** — almost certainly composition change (a bulk-backfill window
vs a review window), not model improvement.
**Fixed, two parts.** (1) `backfill-bulk` now writes `operator_changes: {bulk: true}` on
both paths and `_window_metrics` excludes those rows. (2) Pre-marker rows are
unrecoverable, so the verdict refuses to guess: when ≥75% of decisions are unchanged
approvals **and** none are identifiably bulk (`UNMARKED_BULK_SUSPICION`), it returns
`insufficient_data` with the reason instead of the flattering reading. Self-clearing once
marked rows appear. Verified against the live archive — now returns `insufficient_data`
rather than the `learning +36.8%` it previously claimed.
Also fixed alongside: rows with no classification were scored as 0%-agreement in
`per_category`, creating a phantom `unknown` bucket (n=21) that dragged the breakdown down.
They are unclassifiable, not disagreements — now counted separately in
`per_category._meta.unclassified`.

### A12 — consolidation was the pipeline's dominant cost ✅ (accounting gap remains)
`consolidation/check.py` ran one Haiku judgement per existing record sharing ≥1 keyword.
With `MIN_KEYWORD_OVERLAP=1`, a single common 4-letter word makes a pair eligible, so a
candidate fanned out to ~100 calls against a 50+50 pool — and it grew with the archive
(~87 Haiku calls in 3 min on one live pass, more than Stage 1 + Stage 2 combined).
**Fixed:** rank eligible pairs by keyword overlap, judge only the top
`MAX_JUDGEMENTS_PER_CANDIDATE` (12), early-exit once a ≥0.9 same-incident match settles
the action. Cost is now `O(candidates)` not `O(candidates × archive)`. Proven on the live
archive: 60 eligible → 1 call (early exit); worst case capped at 12. `ops/integrity.py`
backstops any dropped low-overlap dupe.
**Still open:** `ops/backend_health.py` does not count consolidation judgements in its cost
estimate (only Stage 1 calls + Stage 2 drafts), so req #12's guard under-counts by up to
~$0.05/candidate. Bounded and small now that the cap is in place; a proper fix threads a
judgement counter through `IngestionReport`.

### A13 — candidate date never reached the consolidation judge ✅ (reddit residual open)
`write_stage2()`'s result dict carried no `date`/`incident_date` key, and
`consolidation.check` runs on that draft alone — so `_judge_pair` always read the candidate
date as `'unknown'` and lost the date-proximity signal its own system prompt relies on, for
**every source**. It surfaced on reddit: reddit titles are casual and overlap MSM headlines
weakly, so the date was the disambiguator that would have linked a reddit post to its
existing incident instead of minting a duplicate. The reddit scraper and adapter are fine —
22/22 candidates carry `published_at`; the date was dropped downstream.
**Fixed:** `write_stage2` threads `date` into its result when present (never overriding a
real `item['date']` with an empty one in `build_queue_row`'s `{**item, **draft}` merge), and
`_judge_pair` reads `incident_date or date or 'unknown'` so an empty value reads honestly as
unknown. Covered by `test_consolidation_date.py`.
**Reddit post-date-vs-event-date — RESOLVED by reclassifying reddit as a signal.** ✅
`published_at` from reddit RSS is the POST date, not the event date, so using it as the
incident date made old events resurface as new cards. Operator decision (July 2026): reddit
is user-generated discussion, not verifiable journalism — reclassified from `source_type='reddit'`
to `'signal'` (same tier as EDMW). `scrape_reddit` now emits `'signal'`, so a reddit URL is
stripped from `source_urls` (guardrail #2) and a reddit-only item can never publish; MSM is
the sole authority for the citation and the event date. A reddit signal about an existing
incident consolidates as forum buzz / a link rather than a duplicate. Migration **012**
flips the two reddit rows in `sources` to `type='signal'` (defensive: makes `classify()`
resolve reddit domains to signal too). The post date still drives the recency watermark, so
old reddit posts aren't re-fetched. Verified live: 40/40 reddit candidates now come through
as signal.

### Still open from this sweep
- **A6 — ephemeral budget file** 🔴 `ingestion/stage1_daily_usage.json` and
  `classifiers/calibration_log.json` live on Cloud Run's ephemeral disk and reset on
  container replacement, so the Stage 1 RPD ceiling cannot be enforced *across*
  passes in production. Near-harmless at one pass/day (usage is far below the cap and
  the container is replaced between passes anyway); would matter if cadence increases.
  **Fix:** move both to Supabase.
- **A7 — `war-room /api/autonomy` proxy is broken** 🔴 `apps/war-room/app/api/autonomy/route.ts`
  calls `${AGENTS_INTERNAL_URL}/autonomy/status` but sends no `X-Ops-Token`, and
  `AGENTS_INTERNAL_URL` appears nowhere in `.env.local.example` — the documented var
  for this hop is `AGENTS_API_URL`, paired with `OPS_TOKEN`. `main.py`'s
  `/autonomy/status` is `Depends(_require_ops_token)`, so the call fails auth
  whenever the URL *is* configured, and 503s ("not configured") when it isn't. Either
  way the autonomy table on `/analytics` has never rendered live data.
  **Fix:** use `AGENTS_API_URL` and send `X-Ops-Token`, as `lib/artGenerate.ts`
  already does for `/art/generate`.
- **A8 — `autonomy_tracker.py` bypasses the shared client** 🟡
  `packages/agents/classifiers/autonomy_tracker.py` calls `create_client` with
  `os.environ['SUPABASE_URL']` / `os.environ['SUPABASE_SECRET_KEY']` directly, raising
  `KeyError` instead of the friendly `EnvironmentError` every other module raises.
- **A9 — `ingestion/orchestrator.py` is only partly tested** 🟡 `test_watermark_advance.py`
  (2026-07-30) now drives `run_ingestion_pass` end-to-end in both write modes and
  covers the **mid-pass Stage 1 budget halt**, a **per-candidate transient error**,
  and the **cluster-phase deadline abort** — via the watermark each leaves behind,
  which is the observable that matters for "no window is ever skipped". Still
  uncovered: the **circuit breaker**, the pre-fetch **deadline abort**,
  `Stage1HaltError`, and the `dedup.InfraError` whole-pass abort. Note the deadline
  paths cannot be reached through the `now` argument — `run_ingestion_pass` derives
  its deadline from `now` but compares it against the real wall clock, so a test
  must either patch the clock or call `_write_clusters` directly (as that file does).

---

## Suggested execution order

Steps 1, 2 and 4 of the original plan are done — every Critical, every High except
the two operator-run cleanups, and all of M1–M7 landed, with the DB-constraint
fixes bundled into migration 010 as planned (M14 went to code normalisation
instead of a CHECK). What remains, in priority order:

1. **A7** — the autonomy proxy is the only remaining item that makes an operator
   surface lie: `/analytics` renders no live data and has never done so.
2. **H7/H8** — run `cleanup_corrupted.py --apply` (and
   `cleanup_delete_dupe_drafts.py --apply`) against the live DB, or confirm they
   already ran. Corrupt `incident_date` and stale `corroboration_count` are
   reader-visible.
3. **L7/L8** — pipeline safety: a circuit breaker that resets on the highest-volume
   branch is not a circuit breaker, and `record_run` can still throw out of a pass
   that otherwise succeeded.
4. **M15** — the migration runner. Now at 015 with no ledger, and a skipped file
   never announces itself: 009/011 surface as silently rejected inserts, 013 as an
   anon write hole quietly left open.
5. Remaining Medium/Low as a hygiene sweep: M8, M16's `didMount`, L3, L6, L9, plus
   A6, A8, A9 and A12's judgement counter.
