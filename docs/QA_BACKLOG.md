# QA Backlog — Full-Codebase Functional Review (June 2026)

Companion to `POST_LAUNCH_HARDENING.md`. Records the 39 issues surfaced by the
June-2026 full-codebase QA sweep (public web, War Room CMS, agents pipeline, DB/
security) with a tech-lead-approved fix for each, ranked Critical → Low.

**Status:** 🔴 Open · 🟡 Fixed in unmerged PR · 🟢 Cleanup script pending · ✅ Done

> **Implementation status — branch `fix/qa-hardening` (this PR).**
> **Landed:** C1, C2, C4, C3, M5 · H1, H2, H3, H4, H5 · M1, M2, M3, M4, M6, M7,
> M9, M10, M13 · L1, L2, L5, L11. Plus migration **010** (C3/C4/M7) and tests
> (`test_stage2_guardrails.py`). Verified: both apps `tsc --noEmit` clean, Python
> 22/22 tests pass, web+war-room smoke OK.
> **Already in-flight:** H6 (confirm-update date, merged into this branch via PR #10),
> H7/H8 (`cleanup_corrupted.py --apply` — operator-run).
> **Deferred (fast-follow — larger refactors / process, low user impact):** M8
> (VariableSizeList), M12 (gnews canonicalization), M14 (source_type vocab), M15
> (migration runner), M16 (map year-effect race), L3, L4 (won't-fix: "Deaths: 0"
> conveys confirmed-none), L6, L7, L8, L9, L10.

---

## 🔴 Critical

### C1 — Political-content guardrail unenforced 🔴
`filters/stage2_writer.py:325-330,413-423`. Guardrail only logs; no model is told to
emit the `[POLITICAL CONTENT DETECTED — REJECT]` marker, and the final `confidence`
is overwritten by Haiku's value.
**Fix:** add an explicit political-detection instruction to the Haiku classifier
prompt (`political: true` + `confidence: 0`); in `write_stage2`, short-circuit to a
rejected draft (`confidence=0`, reject flag) when `political` is set, **before** the
Haiku-confidence merge. Add a unit test with a political sample asserting `confidence==0`.

### C2 — EDMW URL leaks into `source_urls` 🔴 (latent)
`ingestion/orchestrator.py:212-216`. `edmw` candidates set `source_urls=[candidate.url]`,
violating guardrail #2. Latent (no EDMW adapter yet).
**Fix:** when `source_type=='edmw'`, set `source_urls=[]` (or omit) and rely solely on
`edmw_signal_count`; the operator/Stage-2 must attach an MSM URL before publish. Add a
DB trigger rejecting any `source_urls` element whose domain matches a `sources.type='signal'` row.

### C3 — UTM analytics silently fail (RLS vs anon key) 🔴
`apps/web/lib/supabase.ts` + `api/utm/log/route.ts:37-47`. `utm_events` has RLS on with
no INSERT policy; web uses the anon key → every UTM POST is rejected (500).
**Fix:** give `/api/utm/log` its own server-only admin client (secret key, never imported
by client code) **or** add a tightly-scoped anon `FOR INSERT` policy on `utm_events`
(`WITH CHECK (true)`, no SELECT). Verify against prod which path is live.

### C4 — `source_urls ≥ 1` constraint broken for empty arrays 🔴
`migrations/001:26-27`. `array_length('{}',1)` returns NULL, so `'{}'` passes the CHECK.
**Fix:** new migration — `ALTER TABLE incidents DROP CONSTRAINT … ; ADD CONSTRAINT
incidents_source_urls_nonempty CHECK (cardinality(source_urls) >= 1);`. Audit existing
rows for `'{}'` first.

---

## 🟠 High

### H1 — `confirm-close` never writes the incident 🔴
`war-room/api/queue/[id]/confirm-close/route.ts:30-39`. Operator-confirmed conclusion is
not persisted.
**Fix:** before dismissing, `incidents.update({ is_developing:false,
conclusion_type:'timeout', concluded_at:<source date>, latest_source_role:'timeout' })`
keyed on `rc.incident_id`; capture the error.

### H2 — `approve` swallows 3 post-insert errors 🔴
`war-room/api/queue/[id]/approve/route.ts:117-149`. `incident_links`, queue-status,
`training_signals` errors discarded → double-publish risk.
**Fix:** capture each `{error}`; treat the queue-status update failure as a hard 500
(it governs idempotency); `console.error` the other two. Apply the same to `backfill-bulk` (H/M11).

### H3 — Dateless items stamped with *today* on approve 🔴
`approve/route.ts:50-53`. `_date_fallback` items default `incident_date` to `new Date()`.
**Fix:** when `rc._date_fallback` is set and the operator supplied no date, **block**
approval (422 + message) instead of defaulting to today.

### H4 — Live rows mislabeled `_backfill:true` 🔴
`consolidation/queue_row.py:48-56`. Forward-pipeline rows look like backfill → bulk-approve UI
can mass-approve live drafts.
**Fix:** add `is_backfill: bool = True` param to `build_queue_row`; orchestrator passes
`False`. War Room buckets then reflect reality.

### H5 — Home map shows all-time pins on load, then shrinks to current year 🔴
`app/page.tsx:36-41` vs `api/map`. SSR seeds all-year pins; year effect shrinks them.
**Fix:** scope the SSR map query to `currentYear` (mirror the `gte/lt incident_date`
filter) so initial pins match the default year + sidebar.

### H6 — `confirm-update` date corruption 🟡
`confirm-update/route.ts:63-78`. `new Date()` stamped as timeline + incident_date.
**Status:** fixed in PR #10 (commit on `feat/feed-lightning-developing-timeline`); merge to close.

### H7 — 8 incidents corrupted (`incident_date=2026-06-23`) 🟢
From the War Room merges. **Status:** `cleanup_corrupted.py --apply` pending.

### H8 — `corroboration_count` stale on 13 incidents 🟢
Merges didn't bump the count → lightning under-counts. **Status:** `cleanup_corrupted.py --apply` pending.

---

## 🟡 Medium

- **M1 — Dead "Sev ≥ N" filter** (`timeline/TimelineClient.tsx:32-48`). 🔴
  **Fix:** add a sanitised `min_severity` param to `/api/incidents` (`.gte('severity', n)`) and send it from `loadMore`.
- **M2 — Chaos counts polluted by `custom` class** (`api/chaos/route.ts:44-52`, `page.tsx:94`). 🔴
  **Fix:** only increment `acc[cls]`/`total` when `cls ∈ {heart,clown,dagger}`; count `custom` separately if needed.
- **M3 — `computeChaosScore` NaN on null severity** (`lib/utils.ts:184-191`). 🔴
  **Fix:** `sum + (inc.severity ?? 0) * weight`.
- **M4 — `/api/map` Dec-31 boundary drops year-end pins** (`api/map/route.ts:18-19`). 🔴
  **Fix:** use half-open `.lt('incident_date', '${year+1}-01-01')` to match feed/chaos.
- **M5 — `_classify` int() crash on non-numeric deaths/injuries** (`stage2_writer.py:284-286`). 🔴
  **Fix:** wrap coercion in `try/except (TypeError, ValueError)` → fall back to `None`.
- **M6 — `/api/map` has no rate limiting** (`api/map/route.ts`). 🔴
  **Fix:** add `rateLimit(getIp(req))` guard (spec requires it on all `/api/*`).
- **M7 — `war_room_queue` incident FKs have no `ON DELETE`** (`migrations/001:139`, `002:47`). 🔴
  **Fix:** new migration `ALTER … SET NULL ON DELETE` for `incident_id` + `update_target_incident_id`. (Deletion script currently nullifies manually.)
- **M8 — Fixed `ITEM_HEIGHT=152` clips tall cards** (`IncidentFeed.tsx:8`). 🔴
  **Fix:** move to `VariableSizeList` with measured rows, or enforce a hard max-height clamp on the card.
- **M9 — `confirm-update` links missing `confirmed_by_operator` + swallowed error** (`confirm-update/route.ts:108-118`). 🔴
  **Fix:** add `confirmed_by_operator:true`; capture `{error}`, log when `code !== '23505'`.
- **M10 — `unpublish` has no `is_published` precondition** (`unpublish/route.ts`). 🔴
  **Fix:** `.eq('is_published', true).select('id')`; no-op (skip training signal) when 0 rows.
- **M11 — `backfill-bulk` misreports `updated` + swallows approve errors** (`backfill-bulk/route.ts:72,166`). 🔴
  **Fix:** `.select('id')` on the update and report returned count; capture approve-path queue error into `errors`.
- **M12 — Candidate.url canonicalization violated on resolver failure** (`_gnews_helpers.py:163`, `sources/google_news_rss.py:149`). 🔴
  **Fix:** when resolution still returns a `news.google.com` URL, canonicalize on the article-id blob (so repeat wrappers collapse) or drop the candidate; document dedup is best-effort otherwise.
- **M13 — Queue-dedup asymmetry (`proposed_summary` vs `summary`)** (`consolidation/check.py:191`). 🔴
  **Fix:** fall back to `raw_content.summary` when `proposed_summary` empty; skip judging title-only rows.
- **M14 — `source_type` vocab drift (`edmw`/`rss` vs `signal`)** (`contracts.py:35`, schema). 🔴
  **Fix:** standardise vocabulary (map `edmw→signal` at ingestion) or add aligned CHECKs on `war_room_queue`/`training_signals.source_type`.
- **M15 — No migration runner / 006 saga** (`migrations/006_*`, `009`). 🔴
  **Fix:** adopt Supabase CLI `db push` + a schema-version ledger; delete the superseded `006_ingestion_learning_loop_schema.sql`.
- **M16 — Map year `useEffect` races `load`; filter not re-applied on year change** (`IncidentMap.tsx:177-217`). 🔴
  **Fix:** skip the year-effect's first run (a `didMount` ref); re-apply `activeFilter` after `setData`.

---

## 🔵 Low

- **L1 — `validateUUID` rejects uppercase UUIDs** (`war-room/lib/utils.ts:73`, `backfill-bulk:25`). Add `/i` flag. 🔴
- **L2 — `UTMLogger` ships `console.log`s (incl. response body) to prod** (`UTMLogger.tsx`). Gate behind `NODE_ENV!=='production'`. 🔴
- **L3 — Timeline year input not clamped → silently returns unfiltered archive** (`TimelineClient.tsx:88`). Validate client-side or surface "invalid year". 🔴
- **L4 — Detail page shows "Deaths: 0" when `deaths=0, injuries>0`** (`[slug]/page.tsx:208`). Use `(deaths ?? 0) > 0` on inner spans. 🔴
- **L5 — tz drift: `new Date('YYYY-MM-DD').getFullYear()` parses UTC** (`page.tsx:119`). Parse year from `incident_date.slice(0,4)`. 🔴
- **L6 — `/api/incidents` ignores `limit` param clients send** (`incidents/route.ts`). Honor it (clamped) or drop from clients. 🔴
- **L7 — Circuit breaker resets on Stage-1 reject + only counts rate-limit/billing** (`orchestrator.py:208,296`). Don't clear on reject; broaden `_classify_error` to 5xx/timeout/connection. 🔴
- **L8 — `record_run` outside try/except can violate "never raises"** (`orchestrator.py:353`). Wrap in `try/except` + log. 🔴
- **L9 — In-memory rate limiter is per-instance** (`lib/rateLimit.ts`). Move to Upstash/Redis before relying on it. 🔴 (self-documented)
- **L10 — `pattern_alerts`/`people_profiles` use `USING(true)` policies** (`migrations/003:39`). Scope `TO service_role` or drop (default-deny is stricter). 🔴
- **L11 — `incident_links` query relies on RLS for `confirmed_by_operator` without asserting it** (`[slug]/page.tsx:61`). Add a defensive `.eq('confirmed_by_operator', true)`. 🔴

---

## Addendum — July 2026 autonomy sweep

Found while wiring the daily unattended pass (`docs/AUTONOMY.md`). All five were
live defects, not hypotheticals.

### A1 — `Stage1RpmThrottle.wait_if_needed` could loop forever ✅
`filters/stage1_quota.py`. With `STAGE1_RPM=0`, `len(window) < self._rpm` is never
true, so the throttle slept in 0.1 s steps indefinitely — a genuine infinite loop
reachable from an env-var typo, on the hot path of every Stage 1 call.
**Fixed:** RPM floored at 1; `MAX_WAIT_SECONDS = 90` cap per call. A 429 is
recoverable, a hang is not.

### A2 — Google News consumed the entire pass budget ✅
`ingestion/sources/google_news_rss.py`. `_resolve_redirect` (one HTTP round-trip
each) ran on all ~650 feed entries *before* `RecencyFilter` discarded ~600 of
them as stale. A dry run took **909 s** and hit its deadline inside this source,
so `reddit` and `edmw` never ran at all — the two sources ordered behind it were
being silently starved on every pass.
**Fixed:** filter on the RSS entry's own `pubDate` before resolving; dedup on the
wrapper URL first; `MAX_RESOLVES_PER_FETCH = 120` for cold starts. **909 s → 59 s**,
the remainder being the mandatory politeness delay. Dateless entries are still
never dropped.

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

### A10 — `source_reputation` still cannot populate: `training_signals.source_url` is never written 🔴
Found immediately after 011 landed. `rebuild_source_reputation()` (A4's fix) reads
`training_signals.source_url` — and **all 130 rows have it NULL**, so the rebuild scores
zero domains. The ten War Room routes that insert training signals write
`incident_id`/`action`/`decision`/`operator_changes` but never `source_url` or `queue_id`
(those columns arrived with migration 006 for the ingestion-era schema and no route was
updated). So A4 fixed the writer, but the writer has no input: **the loop is still open.**
**Fix:** populate `queue_id`, `source_url`, `source_type`, `proposed_*` on the four
source-quality routes (`approve`, `reject`, `confirm-update`, `backfill-bulk`).
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

### Still open from this sweep
- **A6 — ephemeral budget file** 🔴 `ingestion/stage1_daily_usage.json` and
  `classifiers/calibration_log.json` live on Cloud Run's ephemeral disk and reset on
  container replacement, so the Stage 1 RPD ceiling cannot be enforced *across*
  passes in production. Near-harmless at one pass/day (usage is far below the cap and
  the container is replaced between passes anyway); would matter if cadence increases.
  **Fix:** move both to Supabase.
- **A7 — `war-room /api/autonomy` proxy is broken** 🔴 `app/api/autonomy/route.ts`
  calls `${AGENTS_INTERNAL_URL}/autonomy/status` but sends no `X-Ops-Token`, and
  `AGENTS_INTERNAL_URL` is in neither `.env.local` nor `.env.local.example`. The
  agents endpoint requires that header, so this returns 401/422 whenever configured.
  The autonomy table on `/analytics` has therefore never rendered live data.
- **A8 — `autonomy_tracker.py` bypasses the shared client** 🟡 Uses
  `os.environ['SUPABASE_URL']` directly, raising `KeyError` instead of the friendly
  `EnvironmentError` every other module raises.
- **A9 — `ingestion/orchestrator.py` has no test** 🟡 No coverage of the circuit
  breaker, deadline abort, `Stage1HaltError`, or the `dedup.InfraError` whole-pass
  abort — the exact paths that keep an unattended pass bounded.

---

## Suggested execution order
1. **C1–C4** — legal guardrails ("never remove") + dead analytics. Highest legal/data risk.
2. **H1–H5** — operator-action data corruption (damage on every click).
3. Merge **PR #10/#11**, run `cleanup_corrupted.py --apply` + `cleanup_delete_dupe_drafts.py --apply` (clears H6–H8).
4. **M1–M5** — user-visible (dead filter, count drift, NaN score, dropped pins, data loss).
5. Remaining Medium/Low as a hygiene sweep; bundle the DB-constraint fixes (C4, M7, M14) into one migration (010).
