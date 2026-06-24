# QA Backlog — Full-Codebase Functional Review (June 2026)

Companion to `POST_LAUNCH_HARDENING.md`. Records the 39 issues surfaced by the
June-2026 full-codebase QA sweep (public web, War Room CMS, agents pipeline, DB/
security) with a tech-lead-approved fix for each, ranked Critical → Low.

**Status:** 🔴 Open · 🟡 Fixed in unmerged PR · 🟢 Cleanup script pending · ✅ Done

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

## Suggested execution order
1. **C1–C4** — legal guardrails ("never remove") + dead analytics. Highest legal/data risk.
2. **H1–H5** — operator-action data corruption (damage on every click).
3. Merge **PR #10/#11**, run `cleanup_corrupted.py --apply` + `cleanup_delete_dupe_drafts.py --apply` (clears H6–H8).
4. **M1–M5** — user-visible (dead filter, count drift, NaN score, dropped pins, data loss).
5. Remaining Medium/Low as a hygiene sweep; bundle the DB-constraint fixes (C4, M7, M14) into one migration (010).
