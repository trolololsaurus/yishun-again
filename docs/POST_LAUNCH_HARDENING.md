# Post-Launch Hardening — Design Document

**Status:** Approved scope, build-in-sequence · **Targets:** TechSpec v1.9+ · **Companion to:** INGESTION_DESIGN.md, LEARNING_LOOP.md

This document specifies the hardening work surfaced by the pre-launch localhost
verification. It addresses six workstreams. The unifying theme: **stop silently
discarding information the human-in-the-loop needs.** The system currently leaks
information at three points — Stage 1 rejects (discarded), in-flight duplicates
(not cross-checked), and four operator actions (not signalled). Closing those
leaks is what makes the Learning Loop real and the archive trustworthy.

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

---

## Launch line

| Workstream | Blocks launch? |
|---|---|
| WS-0 Safety & reconciliation | **YES** — named-crime content live without review |
| WS-A Date recovery + display | **YES** — feed shows wrong/1970 dates |
| WS-D `unpublish` signal (only) | **YES** — strongest correction must be captured before live |
| WS-B Consolidation (in-flight dedup, fuzzy, manual merge) | No — post-launch |
| WS-C Smart reject capture | No — post-launch |
| WS-D remainder (confirm-close/dismiss-link/reopen, reject log) | No — post-launch |

Launch may proceed once WS-0, WS-A, and the WS-D `unpublish` signal are done.
WS-B/C/D-remainder are staged post-launch builds.

---

## WS-0 — Safety & Reconciliation (LAUNCH BLOCKER)

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

1. **Card display:** the visible primary date must be `incident_date` (the real
   article/event date), NOT `published_at` (operator approve-click time, often
   NULL→1970). `published_at` becomes internal approve-tracking only.
2. **Recover/fill dates for the 28 bypass rows:** for each, derive the correct
   date from `incident_date` (already correct) and cross-check against
   `raw_content.date` where a queue row exists. Where `published_at` is needed
   for sort/tracking, set it sensibly (not NULL).
3. **War Room eyeball:** surface the 28 with their recovered dates for operator
   confirmation. **This confirmation is a training signal** (operator validated
   the date) — write it per WS-D.
4. **Feed sort fix:** `ORDER BY ... published_at DESC` → add `NULLS LAST` and an
   `id` tiebreaker (`.order('id', {ascending:false})`) to kill the duplicate-slug
   pagination bug from tied timestamps.
5. **Null-display guard:** `fmtDate(null)` must render "—" or the `incident_date`,
   never epoch/1970.

### Rendering bugs (bundle here — trivial)
- **Tailwind colors:** add `'./lib/**/*.{ts,tsx}'` to `apps/web/tailwind.config.js`
  content array so the classification color classes (`text-good-vibes` etc.)
  actually generate. Currently all icons render parchment.

---

## WS-B — Consolidation: in-flight dedup + fuzzy + manual merge (POST-LAUNCH)

The car-rammer case (5 incidents, 1 event) exposed a structural gap.

### B.1 In-flight queue dedup (the structural fix)
`consolidation.check()` currently matches only against already-**published**
`incidents`. Same-batch duplicates (all arriving before any is published) have
nothing to match against. **Fix:** consolidation must also check against pending
items already in `war_room_queue` from the same/recent pass — so item 2 sees
item 1 even before either is approved.

### B.2 Fuzzy / semantic matching
URL-exact dedup misses same-event-different-headline. Add semantic matching
(title + incident_date + location proximity) so "rubbish chute crash meth driver"
and "drugged unlicensed driver crashes void deck" recognise as one event. Keep a
confidence threshold; below it, surface as a *suggested* merge for the operator
rather than auto-merging (avoid false merges).

### B.3 Canonical URL resolution
Stored URL is a Google News redirect, not the real article URL — which also
defeats URL dedup. Resolve redirects to the canonical article URL at ingestion
(the `_resolve_redirect` helper exists; ensure it runs and persists the resolved
URL). Improves both dedup and source attribution.

### B.4 Manual merge/link tool (War Room) — operator currently CANNOT do this
A War Room UI to manually merge/link incidents the agent missed: select two+
incidents → merge into one (enriching timeline, preserving all sources) or link
as related. **This action is a high-value training signal** (the agent missed a
merge the human caught) — write per WS-D.

---

## WS-C — Smart reject capture (POST-LAUNCH)

**NOT all Stage 1 rejects — quality over volume.** Most Stage 1 rejects are
confident rubbish (property listings at confidence 0.00); training on them is
rubbish-in-rubbish-out and dilutes real signal.

### C.1 Boundary-confidence reject log
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

### C.2 Resurface wrongly-rejected items (War Room)
A War Room view to browse captured rejects (boundary Stage-1 + operator rejects)
and **resurface** ones that were wrongly rejected back into the queue. Recovering
a wrongly-rejected item is the **highest-value correction signal** (the agent/operator
was wrong — here's the truth). Write per WS-D.

---

## WS-D — Complete signal capture (unpublish = LAUNCH BLOCKER; rest POST-LAUNCH)

Every human-in-the-loop action must become training signal. Current gaps:

| Action | Signal today | Fix |
|---|---|---|
| `unpublish` | **none** | **LAUNCH BLOCKER** — taking live content offline is the strongest "this was wrong" signal; must be captured before going live |
| `confirm-close` | none | write `decision='approve'` (AI's auto-conclusion confirmed correct) |
| `dismiss-link` | none | write `decision='reject'` (consistent with dismiss-alert) |
| `reopen` | none | write a correction signal (AI's conclusion was wrong) |
| date-eyeball (WS-A) | n/a | operator confirming a recovered date = validation signal |
| manual merge (WS-B.4) | n/a | agent missed a merge = high-value signal |
| resurface reject (WS-C.2) | n/a | wrongly-rejected recovery = highest-value signal |
| boundary Stage-1 rejects (WS-C.1) | n/a | feed the reject log |

All signals use the existing `training_signals` schema (migration 006/007). The
`decision` column is NOT NULL — every writer supplies it.

---

## Build order

1. **WS-0** safety (unpublish 5 + JI dup + Kurt Tay) — immediate
2. **WS-D `unpublish` signal** — small, launch-blocking
3. **WS-A** date recovery + display + rendering bugs — launch-blocking
4. → **LAUNCH** (public site)
5. **WS-D remainder** (confirm-close/dismiss-link/reopen) — small
6. **WS-C** smart reject capture + resurface view
7. **WS-B** consolidation (in-flight dedup → fuzzy → canonical URL → manual merge) — the big one
8. Each verified before the next; design-doc-first for WS-B sub-parts if they grow.

---

## Principles preserved
- **Victim safety non-negotiable** — named-crime content always passes the gate.
- **Quality over volume in training data** — capture uncertain cases, not rubbish.
- **Human corrections are the highest-value signal** — recovery/merge/unpublish
  teach more than routine approvals.
- **Human-in-the-loop never removed** — these changes deepen the loop, never bypass it.
- **No silent discards** — close the three information leaks.
