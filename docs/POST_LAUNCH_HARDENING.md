# Post-Launch Hardening — Design Document

**Status:** Partly landed — item-by-item status verified against the working tree
2026-08-02 · **Targets:** TechSpec v1.9+ · **Companion to:** INGESTION_DESIGN.md,
LEARNING_LOOP.md, QA_BACKLOG.md

This document specifies the hardening work surfaced by the pre-launch localhost
verification. It addresses five workstreams (WS-0, WS-A, WS-B, WS-C, WS-D). The
unifying theme: **stop silently discarding information the human-in-the-loop
needs.** The system leaked information at three points — Stage 1 rejects
(discarded), in-flight duplicates (not cross-checked), and four operator actions
(not signalled). Closing those leaks is what makes the Learning Loop real and the
archive trustworthy.

**Where the three leaks stand (2026-08-02):** in-flight duplicates are now
cross-checked (WS-B.1, landed); one of the four operator actions is signalled
(WS-D `unpublish`, landed); Stage 1 rejects are still discarded (WS-C, open).

**Status marks used below:** ✅ landed, with the code reference · ◐ partly landed
· 🔴 open · ⬚ operator/DB action, no code to verify against.

---

## 0. Root cause (read first)

The pre-launch audit found 28 published incidents that **bypassed the War Room
approve flow** — bulk-inserted directly into `incidents` with `is_published=true`
by the 2026-06-12 deep-backfill script. Consequences:
- `published_at = NULL` → cards show "01 Jan 1970", and NULL sorts first so they
  float to the top of the feed
- **5 of them name individuals in serious crimes** and never passed victim-safety
  review (the review IS the War Room approve step they skipped)

The dates themselves are largely fine: `incident_date` is correct and populated
on all 93 rows, and the real article date is recoverable from
`raw_content.date`. The problem is process (gate bypassed) and display (wrong
field shown), not corrupt data.

**✅ The recurrence path is closed.** Both publish paths now stamp a non-NULL
`published_at` and refuse to invent an `incident_date`:
`apps/war-room/app/api/queue/[id]/approve/route.ts:138-140` (the stamp) with
:70-86 returning a 400 rather than defaulting the date to today (QA H3), and
`packages/agents/ops/auto_publish.py:393-395`. A new row can no longer reach the
feed with a NULL `published_at` or an invented `incident_date`. The 28 historical
rows are DB state — see WS-0/WS-A.

---

## Launch line

| Workstream | Blocks launch? | Status (2026-08-02) |
|---|---|---|
| WS-0 Safety & reconciliation | **YES** — named-crime content live without review | ⬚ DB actions — the unpublish *mechanism* exists and is signalled |
| WS-A Date recovery + display | **YES** — feed shows wrong/1970 dates | ✅ display + sort + rendering fixes landed; ⬚ the 28-row backfill is DB state |
| WS-D `unpublish` signal (only) | **YES** — strongest correction must be captured before live | ✅ landed |
| WS-B Consolidation (in-flight dedup, fuzzy, manual merge) | No — post-launch | ◐ B.1/B.2/B.3 landed, B.4 open |
| WS-C Smart reject capture | No — post-launch | 🔴 open |
| WS-D remainder (confirm-close/dismiss-link/reopen, reject log) | No — post-launch | 🔴 open |

Launch may proceed once WS-0, WS-A, and the WS-D `unpublish` signal are done.
WS-B/C/D-remainder are staged post-launch builds.

---

## WS-0 — Safety & Reconciliation (LAUNCH BLOCKER)

> ⬚ **These are row-level operations on the live database.** The working tree
> carries no record of whether they were executed, so nothing below is marked
> done from code. What *is* verifiable: the mechanism 0.1 asks for exists and is
> instrumented — `apps/war-room/app/api/incidents/[id]/unpublish/route.ts` sets
> `is_published=false, published_at=null` under a compare-and-set and writes the
> `unpublish` training signal (WS-D). Two repo-side residues are recorded inline
> below.

### 0.1 Named-crime bypass rows — unpublish NOW, then recover+review
These 5 are live, name real people in serious crimes, and never passed
victim-safety review. **Set `is_published=false` immediately**, then route each
through WS-A's find-date → War Room eyeball flow before any re-publish. Victim
safety re-checked on review.
- `yishun-cat-case-lee-wai-leong-2015`
- `yishun-fatal-assault-shawn-rodrigues-2016`
- `yishun-ring-road-killing-sri-idayu-2016`
- `safra-yishun-student-death-jethro-puah-2021`
- `yishun-ring-road-murder-fiqri-choo-2024`

### 0.2 JI/al-Qaeda duplicate
Two `is_published=true` rows for the same 2001 plot:
- KEEP `yishun-mrt-ji-alqaeda-plot-2001` (correct framing; fix its NULL date via WS-A)
- UNPUBLISH/REMOVE `yishun-mrt-al-qaeda-jemaah-islamiyah-terror-plot-1997`
  (wrong 1997 date, duplicate). Preserve any unique sources onto the kept row first.

### 0.3 Full Kurt Tay reconciliation
- **DELETE** `kurt-tay-intimate-video-case-2023-2026` (old first-gen placeholder duplicate).
  ⚠️ **Repo-side residue:** the slug is still seeded `is_published = TRUE` by
  `packages/db/migrations/005_hero_incidents.sql:511`, so a rebuild-from-migrations
  recreates the row a delete removes; and it is still listed in
  `packages/agents/scrapers/backfill_agent.py:1798` (`--validate-heroes`
  `HERO_SLUGS`). `docs/INGESTION_CHANGELOG.md` also still carries the duplicate as
  an open operator item. Whichever way the live row is resolved, those three need
  to agree with it.
- **PUBLISH** `yishun-kurt-tay-intimate-image-conviction-2026` (verified). The
  conviction is a concluded public court case (CNA, sentenced) — offender named
  (public record), **victim never named, content never described, no prurient
  framing**. Published live per operator decision.
- **REVIEW** `kurt-tay-yishun-character-hub` (currently in the bypass batch) via
  WS-A flow; keep as the CULTURE hub.
- Resolve the 2 remaining Kurt Tay drafts + 6 `incident_links`.
- Confirm cluster: hub + 3 incidents (victim-of-flyers 2015–18 / void-deck
  participant 2022 / intimate-image offender 2026), each with correct role and
  victim-safety applied.

### 0.4 The other 23 bypass rows (non-crime: viral, CULTURE, cat-phenomena)
Lower risk — **stay live**, but go through WS-A (recover date, show in War Room
for eyeball). No victim-safety stakes, so they don't need unpublishing first.

---

## WS-A — Date recovery + display correctness (LAUNCH BLOCKER)

**The data is mostly right; the display is wrong.** `incident_date` already holds
the real article/event date. The fixes:

1. ✅ **Card display:** the visible primary date must be `incident_date` (the real
   article/event date), NOT `published_at` (operator approve-click time, often
   NULL→1970). `published_at` becomes internal approve-tracking only.
   **Landed** — `apps/web/components/IncidentCard.tsx:86` renders
   `fmtDate(incident_date)` on an ordinary card; a concluded card instead shows
   `first_reported_at` → the verdict date taken from `source_timeline` (:98-109).
   Neither path touches `published_at` — it is still selected into the row type
   but is not rendered. The detail page uses `incident_date` too
   (`apps/web/app/incidents/[slug]/page.tsx:178`, and as `datePublished` in the
   schema markup at :117). The War Room list deliberately still shows
   `published_at` (`apps/war-room/app/incidents/page.tsx:97-99`) — that is the
   approve-tracking view, and it renders "—" when NULL rather than an epoch date.
2. ⬚ **Recover/fill dates for the 28 bypass rows:** for each, derive the correct
   date from `incident_date` (already correct) and cross-check against
   `raw_content.date` where a queue row exists. Where `published_at` is needed
   for sort/tracking, set it sensibly (not NULL). *DB state — not verifiable from
   the tree.*
3. 🔴 **War Room eyeball:** surface the 28 with their recovered dates for operator
   confirmation. **This confirmation is a training signal** (operator validated
   the date) — write it per WS-D. *No such view exists; no writer emits a
   date-validation signal.*
4. ✅ **Feed sort fix:** `ORDER BY ... published_at DESC` → add `NULLS LAST` and an
   `id` tiebreaker (`.order('id', {ascending:false})`) to kill the duplicate-slug
   pagination bug from tied timestamps.
   **Landed, and it went further than the plan:** the feed now sorts on
   `incident_date DESC` (`nullsFirst: false`) with the `id` tiebreaker, not on
   `published_at` at all — `apps/web/app/api/incidents/route.ts:34-35` and the SSR
   first page at `apps/web/app/page.tsx:83-84`. The two MUST stay identical or
   SSR page 0 and the load-more pages disagree. `apps/web/app/sitemap.ts:10-13`
   sorts the same way and falls back `published_at ?? incident_date`.
5. ✅ **Null-display guard:** `fmtDate(null)` must render "—" or the `incident_date`,
   never epoch/1970. **Landed** — `apps/web/lib/utils.ts:116-124` returns `'—'`
   for null/undefined/empty before constructing a `Date`.

### Rendering bugs (bundle here — trivial)
- ✅ **Tailwind colors:** add `'./lib/**/*.{ts,tsx}'` to `apps/web/tailwind.config.js`
  content array so the classification color classes (`text-good-vibes` etc.)
  actually generate. Was: all icons rendered parchment.
  **Landed** — the path is in the `content` array; `good-vibes` / `absurdities` /
  `dark-events` / `culture` are all defined under `theme.extend.colors`.

---

## WS-B — Consolidation: in-flight dedup + fuzzy + manual merge (POST-LAUNCH)

The car-rammer case (5 incidents, 1 event) exposed a structural gap.

### B.1 ✅ In-flight queue dedup (the structural fix)
`consolidation.check()` matched only against already-**published** `incidents`.
Same-batch duplicates (all arriving before any is published) had nothing to match
against. **Fix:** consolidation must also check against pending items already in
`war_room_queue` from the same/recent pass — so item 2 sees item 1 even before
either is approved.

**Landed** — `packages/agents/consolidation/check.py:296` (`_fetch_recent_queue`,
`processed_at IS NULL` only, `QUEUE_FETCH_LIMIT = 50`). The two pools are scored
together and judged together; a same-event match against a *queued* sibling
returns `action='skip'` rather than `'update'` (:469-483), because there is no
published row to update — an equivalent report is already awaiting review.
Rejected queue items are deliberately ignored, and approved ones are covered by
the published pool. Guard: `packages/agents/test_consolidation_queue_dedup.py`
(skip / update / new / weak-match-is-new).

### B.2 ✅ Fuzzy / semantic matching
URL-exact dedup misses same-event-different-headline. Add semantic matching
(title + incident_date + location proximity) so "rubbish chute crash meth driver"
and "drugged unlicensed driver crashes void deck" recognise as one event. Keep a
confidence threshold; below it, surface as a *suggested* merge for the operator
rather than auto-merging (avoid false merges).

**Landed** — `consolidation/check.py::_judge_batch` sends the candidate and the
whole eligible pool to Haiku in **one** call and asks which record, if any, is
the same event; entity/act/date/block reasoning is in the prompt (:184-220).
Location is prompt-level reasoning ("same kind of act at a different block or
street … is NOT the same event"), not a numeric distance. A cheap keyword
pre-filter (`MIN_KEYWORD_OVERLAP = 1`) decides who is offered to the judge and
ranks them; it no longer caps a call count, so the long tail is judged rather
than dropped. Thresholds in `consolidation/rules.py`: `UPDATE_MATCH_THRESHOLD =
0.7` merges, `WEAK_MATCH_THRESHOLD = 0.4` surfaces the pair as a low-confidence
related link for the operator instead of auto-merging, `RELATED_LINK_THRESHOLD =
0.5` records a related link. A failed judgement fails **open** (treated as new):
a duplicate an operator can merge, never a silently dropped story, with
`ops/integrity.py` re-scanning for duplicates as the backstop.

### B.3 ✅ Canonical URL resolution — solved by removing the redirector, not by resolving it
Stored URL was a Google News redirect, not the real article URL — which also
defeated URL dedup. The plan was to resolve redirects at ingestion via the
`_resolve_redirect` helper.

**That approach was abandoned on 2026-08-02 and the source was deleted instead.**
`news.google.com/rss/articles/<blob>` wrappers do not HTTP-redirect; decoding one
needs a reverse-engineered `batchexecute` RPC that Google rotates. When resolution
failed the **wrapper** was stored as the candidate URL, which broke dedup (it
matches on URL), put a redirect where a citation belongs in
`war_room_queue.source_url` and `incidents.source_urls`, and tripped
`unapproved_source_domain`. Two live rows on 2026-08-01 demonstrated all three.

What replaced it, all emitting the **publisher's own** URL:
- `packages/agents/ingestion/sources/news_sitemap.py` — each outlet's own
  Google-News sitemap, 9 outlets. A far wider window than the front-page feeds
  (Straits Times: 462 sitemap entries against 44 in its RSS).
- `packages/agents/ingestion/sources/wp_search.py` — `?s=yishun&feed=rss2` over
  the whole archive, 2 sites (MustShareNews, The Independent).

And the net underneath: `classifiers/source_allowlist.py` gained
`REDIRECT_DOMAINS` + `is_redirect_domain()` (:101-120). `classify()` returns
`'redirect' | 'signal' | 'approved' | 'unapproved'` and checks redirect **first,
without consulting the `sources` table**, so the rule cannot be defeated by adding
the host to `sources`. `check_source_urls()` returns a `dropped_redirect` key
(:185-235), and `consolidation/queue_row.py:107-115` substitutes a real publisher
URL for a redirector `source_url`. `_resolve_redirect` still exists in
`scrapers/_gnews_helpers.py` but is now reached only from the manual
`backfill_agent.py` and the one-off cleanup scripts — never the live pass.

### B.4 🔴 Manual merge/link tool (War Room) — operator still CANNOT do this
A War Room UI to manually merge/link incidents the agent missed: select two+
incidents → merge into one (enriching timeline, preserving all sources) or link
as related. **This action is a high-value training signal** (the agent missed a
merge the human caught) — write per WS-D.

**Still open.** What exists is adjacent but not this: `queue/[id]/confirm-link`
and `dismiss-link` act only on links the *agent* proposed; `confirm-update` merges
a queue item into its agent-chosen target incident; `split` splits one queue item.
None of them lets an operator pick two arbitrary incidents and merge them.
`ops/integrity.py:796` auto-rejects queue duplicates it finds and writes the
reason as a `decided_by='agent'` training signal — a backstop for the agent's own
misses, not an operator tool.

---

## WS-C — Smart reject capture (POST-LAUNCH)

**NOT all Stage 1 rejects — quality over volume.** Most Stage 1 rejects are
confident rubbish (property listings at confidence 0.00); training on them is
rubbish-in-rubbish-out and dilutes real signal.

### C.1 🔴 Boundary-confidence reject log
Log a Stage 1 reject ONLY when it's near the decision boundary — the agent's
*uncertain* cases, where it might be wrong:
- capture if Stage 1 confidence is within the boundary band (config constant,
  initial `REJECT_CAPTURE_BAND = (0.3, 0.7)`)
- ALWAYS capture if the override rule fired but didn't win
- DISCARD confident rejects (< band low) — but keep a lightweight daily
  AGGREGATE count (e.g. "rejected 142, 8 borderline") for observability, not
  per-row storage
The band is a tunable constant — adjust once real volume is observed
(build-towards-utopia, tune with data).

**Still open.** `REJECT_CAPTURE_BAND` does not exist anywhere in the codebase. A
Stage 1 reject is still discarded in place —
`packages/agents/ingestion/orchestrator.py:757-765` (`if not s1["passes"]:` →
`tracker.decided(candidate)` → `continue`), with no row written and no confidence
retained.

Two things have changed around it that alter what remains to be built:
- **The cost half of the problem is already closed.** That `tracker.decided()`
  call is the recency watermark recording the reject as a *decision*, so the same
  article is not re-bought from Gemini on every pass — historically the largest
  share of the bleed. What is still missing is the *training* half: the reject's
  content and confidence are not kept.
- **The aggregate the third bullet asks for exists per source per pass.**
  `packages/agents/ingestion/health.py::record` writes `items_found` (candidates
  surviving the Yishun keyword filter) and `items_passed_s1` to `scraper_health`,
  so rejects = found − passed is already observable in the War Room health views.
  It does not distinguish borderline from confident rejects.

### C.2 🔴 Resurface wrongly-rejected items (War Room)
A War Room view to browse captured rejects (boundary Stage-1 + operator rejects)
and **resurface** ones that were wrongly rejected back into the queue. Recovering
a wrongly-rejected item is the **highest-value correction signal** (the agent/operator
was wrong — here's the truth). Write per WS-D.

**Still open.** `apps/war-room/app/api/queue/route.ts:8` reads only
`status IN ('pending','update')`, so rejected rows are not fetched by any view,
and there is no route that moves a rejected row back to pending.

---

## WS-D — Complete signal capture (unpublish = LAUNCH BLOCKER; rest POST-LAUNCH)

Every human-in-the-loop action must become training signal. Status per action
(verified 2026-08-02):

| Action | Signal today | Fix |
|---|---|---|
| `unpublish` | ✅ `action='unpublish'`, `decision='reject'` — `apps/war-room/app/api/incidents/[id]/unpublish/route.ts:48` | **Done.** Taking live content offline is the strongest "this was wrong" signal, so it shipped before launch |
| `confirm-close` | 🔴 none — `queue/[id]/confirm-close/route.ts` | write `decision='approve'` (AI's auto-conclusion confirmed correct) |
| `dismiss-link` | 🔴 none — `queue/[id]/dismiss-link/route.ts` | write `decision='reject'` (consistent with dismiss-alert) |
| `reopen` | 🔴 none — `queue/[id]/reopen/route.ts` | write a correction signal (AI's conclusion was wrong) |
| date-eyeball (WS-A) | 🔴 n/a | operator confirming a recovered date = validation signal |
| manual merge (WS-B.4) | 🔴 n/a | agent missed a merge = high-value signal |
| resurface reject (WS-C.2) | 🔴 n/a | wrongly-rejected recovery = highest-value signal |
| boundary Stage-1 rejects (WS-C.1) | 🔴 n/a | feed the reject log |

`confirm-link` is in the same state as `dismiss-link` — it writes no signal
either, so the operator's *agreement* with a suggested link is as invisible as
their rejection of it. Fix them together.

The `unpublish` writer guards against double-counting: it no-ops if the row is
already a draft, and only the request whose compare-and-set actually matched a
row logs the signal (QA M10). The insert is treated as telemetry — a failure is
logged, not returned — because the unpublish itself has already committed.

All signals use the existing `training_signals` schema. The `decision` column is
NOT NULL (migration **007**) — every writer supplies it. Two later migrations
matter here: **009** added `'unpublish'` to the `action` CHECK (before it, those
inserts were silently rejected by Postgres and swallowed by supabase-js — the
exact failure mode that makes this workstream look done when it is not), and
**011** added `auto_approve` / `auto_publish_reverted` plus `decided_by`
(`operator` | `agent`). Any new writer added here must land its CHECK value in a
migration first.

**Agents write signals too, and must stay marked as agents.**
`ops/auto_publish.py:473` and `ops/integrity.py:823` both insert with
`decided_by='agent'` so autonomous decisions stay out of the operator
agreement-rate maths in `learning_snapshots` — without that flag the fleet grades
its own homework and agreement reads 100% forever.

---

## Build order

1. ⬚ **WS-0** safety (unpublish 5 + JI dup + Kurt Tay) — immediate. *DB actions;
   the tool and its signal exist. Repo residue at WS-0.3 still to reconcile.*
2. ✅ **WS-D `unpublish` signal** — small, launch-blocking
3. ✅ **WS-A** display + sort + rendering bugs — launch-blocking (the 28-row date
   backfill and its War Room eyeball are still outstanding)
4. → **LAUNCH** (public site)
5. 🔴 **WS-D remainder** (confirm-close/dismiss-link/reopen, plus confirm-link) — small
6. 🔴 **WS-C** smart reject capture + resurface view
7. ◐ **WS-B** consolidation — **in-flight dedup, fuzzy matching and canonical URL
   all landed out of order** (the canonical-URL problem was closed on 2026-08-02
   by deleting the Google News source, ahead of this queue). **Manual merge is
   what remains**, and it is now the largest single item in this document.
8. Each verified before the next; design-doc-first for WS-B sub-parts if they grow.

---

## Principles preserved
- **Victim safety non-negotiable** — named-crime content always passes the gate.
- **Quality over volume in training data** — capture uncertain cases, not rubbish.
- **Human corrections are the highest-value signal** — recovery/merge/unpublish
  teach more than routine approvals.
- **Human-in-the-loop never removed** — these changes deepen the loop, never bypass it.
  (Auto-publish at confidence ≥ 0.95 was added later under `docs/AUTONOMY.md`; it
  records `decided_by='agent'` and stays reversible via `unpublish`, which is the
  loop, not a bypass of it.)
- **No silent discards** — close the three information leaks. One of the three is
  still open: Stage 1 rejects (WS-C).
