# Learning Loop — Design Document

**Status:** Phase 1 is built and runs daily · Phases 2–3 remain roadmap · **Targets:** TechSpec v1.9 (links §4.9, §13d) · **Companion to:** `INGESTION_DESIGN.md`, `AUTONOMY.md` §4
**No self-modification. The agent accumulates data; it never rewrites its own weights or code.**

---

## 0. The North Star, and how this document serves it

**North Star:** an autonomous agent that maintains Yishun Again with minimal human intervention.

**This document specifies the mechanism that gets there.** The Learning Loop *is* the road to the
North Star — they are not separate ideas. The loop is:

```
agent proposes → human judges in War Room → judgment becomes training signal
   → agent improves → human judges LESS of the easy stuff over time
```

That final clause — "human judges less of the easy stuff over time" — is precisely what
"minimal intervention" means. The loop is the engine; the North Star is the destination.

### The invariant that keeps it honest (read this twice)

**The human-in-the-loop is never removed — not even at full maturity ("Skynet mode").**
What shrinks is the *volume* of human review, never its *authority*.

- The agent earns the right to handle **easy, high-agreement** content with lighter review
  (a viral oddity, a routine fire).
- The human reviews **hard, high-stakes** content **every time, forever** — crime, named
  individuals, anything novel, anything the agent itself flags low-confidence.
- Even on auto-handled content, the human keeps **generating training signal** by spot-checking,
  correcting drift, and judging new edge cases.

So "minimal intervention" = **minimal volume, undiminished authority.** The human is *elevated*
from data-entry clerk to editor-in-chief — not removed. An agent that removed the human entirely
would stop receiving signal, drift, and rot. The permanent human loop is not in tension with the
North Star; **it is the guardrail that keeps autonomy from decaying into abdication.** This
distinction — autonomy (human does only what needs a human) vs. abdication (no human, silent
rot) — is the spine of everything below.

> ### ⚠️ Operator override — July 2026 (recorded, not silently applied)
>
> The paragraph above says crime and named-individual content are reviewed "every time,
> forever," and §3's Phase-2 design would have made them permanently ineligible for auto-publish.
> **The operator has deliberately overridden that.** Auto-publish (`ops/auto_publish.py`) is a literal
> `agent_confidence >= 0.95` threshold with **no classification carve-out** — dagger content and
> named individuals auto-publish like anything else when they clear the bar.
>
> This is the site owner's editorial call, taken with the tradeoff stated. It is written here
> rather than quietly implemented, because a doc that contradicts the code is worse than either.
>
> **What the override does NOT touch.** The hardcoded legal guardrails still hold, and the
> confidence threshold cannot bypass them: ≥1 source URL (guardrail #1, also a DB CHECK), no
> `type='signal'` URL ever quoted (#2), political content forced to `confidence = 0` by Stage 2 so
> it can never reach 0.95 (#4, re-asserted in `check_eligibility` as defence in depth). On top of
> those sit the data-integrity preconditions the human approve route already enforced: a real
> `incident_date` (never "today"), an operator-approved source domain, and the two deterministic
> post-checks — groundedness and casualty counts. A draft failing any gate is **never rejected** —
> it waits for the operator. The failure mode stays "a human looks at it".
>
> One caveat found on 2026-08-02 and fixed the same day: guardrail #4's *alerting* was reachable
> only when the model returned a classification string. On political stories it tends to return
> `"classification": null`, which threw inside `_classify` before the guardrail ran — the candidate
> died on an exception, so nothing auto-published, but no reject marker, operator email or
> `agent_events` row was raised either. The guardrail is now evaluated before any field
> validation. Guard: `test_stage2_guardrails.py`.
>
> **The human loop itself is intact and still load-bearing**, which is what keeps this an
> override rather than an abandonment of §0: every auto-publish writes a `training_signals` row
> (`decided_by='agent'`), and an operator unpublishing an auto-published incident is the
> correction signal `learning_snapshots.auto_publish_reverted` tracks. Rising reverts mean 0.95
> was too low. Reverting the override is one env var: `AUTO_PUBLISH_CONFIDENCE=2.0`.
>
> See `docs/AUTONOMY.md` §2 for the full gate table.

### Build philosophy: towards utopia, not utopia on day one

Every phase ships in its simplest *real* form, behind an interface that can grow toward the
ambition without a rewrite. The near-term simple thing is deliberately designed to **feed** the
long-term ambitious thing — so progress compounds instead of requiring rework. Concretely: the
contextual-learning phase records training signal in a shape a future fine-tune can consume
directly, so the ambitious phase is "point a job at data that already exists," not "re-instrument
everything."

---

## 1. Two agents, one loop

Per the operator's model, the system has two distinct agents (TechSpec §4.x):

| Agent | Scope | Job | Does it "learn"? |
|---|---|---|---|
| **Historical agent** | Cold Start (1980–2023), Warm Start (2024–Jun 2026) | Enrichment & discovery against the hand-built archive: cross-check existing source links, hunt additional corroboration, enrich existing stories, detect items belonging under an existing umbrella (Kurt Tay, cat killers). NOT bulk re-scraping (proven impossible). | Indirectly — it produces candidates; the human's judgements on them feed the loop. |
| **Futurist agent** | Forward (Jun 2026 →) | The daily live ingestion pass (`run_ingestion_pass()`, §4.9). SG MSM primary; the wider net behind it is each publisher's own news sitemap plus WordPress `?s=yishun&feed=rss2` search feeds. (The Google News RSS aggregator was removed from the live pass on 2026-08-02: its `news.google.com/rss/articles/<blob>` wrappers often failed to resolve, and the unresolved wrapper was then stored as the candidate URL — breaking dedupe and putting a redirect where a citation belongs. `classifiers/source_allowlist` now classifies redirector domains before it consults the `sources` table, so the rule cannot be defeated by adding the host. The historical backfill agent still uses Google News *search* separately.) **This is the agent the Learning Loop trains.** | **Yes — this is the learning subject.** |

The Learning Loop below is owned by the **Futurist agent**. The Historical agent contributes
signal (its proposed enrichments/links get human verdicts too) but is not itself "graduated."

---

## 2. Phase 1 — Contextual learning (BUILT — runs every pass)

The agent improves **without its model weights changing.** Accumulated signal is read back into
prompts and scoring each run. Immediate effect, zero training infrastructure, frozen models
(Gemini `gemini-3.1-flash-lite` for Stage 1 — Groq was retired; Claude Haiku for Stage 2, both
the classify and the write call). **Safe by construction: data in, behaviour steered, no
self-modification.**

Both halves are live: `ingestion/learning.py` is the read side, called from
`run_ingestion_pass()`; `ops/learning_monitor.py` is the write side, step 6 of the daily chain
in `ops/daily.py`.

### 2.1 What War Room records (the signal schema)

Every operator action becomes a structured `training_signal`. This extends the existing
`training_signals` table (TechSpec §3.4) — the shape is chosen so a future fine-tune (Phase 3)
can consume it directly.

```sql
-- SHIPPED, and as an extension rather than a second table: migration
-- 006_phase1_apply_now.sql ALTERs the TechSpec §3.4 table that 001 created
-- (Q-L1 resolved — one training-signal table, not two). The columns below are
-- the decision-relevant subset; 001's own columns (action, reject_reason,
-- original_draft / edited_draft, original_ / edited_classification,
-- original_ / edited_severity, operator_changes, agent_confidence_was) are
-- still present and still written by the War Room routes.
ALTER TABLE training_signals
  -- WHAT the agent proposed (the input the model saw + its output)
  ADD COLUMN queue_id                 UUID,      -- war_room_queue row judged
  ADD COLUMN source_url               TEXT,
  ADD COLUMN source_name              TEXT,
  ADD COLUMN source_type              TEXT,      -- 'msm' | 'signal' (reddit
                                                 -- moved to signal, mig. 012)
  ADD COLUMN proposed_classification  TEXT,
  ADD COLUMN proposed_severity        INTEGER,
  ADD COLUMN agent_confidence         DECIMAL(3,2),

  -- WHAT was decided (the label). NOT NULL since migration 007.
  ADD COLUMN decision                 TEXT
       CHECK (decision IN ('approve','reject','approve_with_edits',
                           're_source','link_umbrella','escalate',
                           'auto_approve')),     -- 'auto_approve' added by 011

  -- corrections (intended to be populated when the human changed something)
  ADD COLUMN corrected_classification TEXT,
  ADD COLUMN corrected_severity       INTEGER,
  ADD COLUMN operator_added_source    TEXT,      -- the human's better source
  ADD COLUMN linked_incident_id       UUID,      -- existing umbrella/hub
  ADD COLUMN operator_note            TEXT;

-- Migration 011: who decided. `decided_by='agent'` rows are excluded from every
-- operator-agreement calculation, or the agent grades its own homework.
ALTER TABLE training_signals
  ADD COLUMN decided_by TEXT NOT NULL DEFAULT 'operator'
       CHECK (decided_by IN ('operator','agent'));
```

**Which of these columns actually carry signal today.** Every War Room route writes `decision`,
`source_url`, `source_type`, `proposed_*`, `original_*` and — on an edit-approve —
`edited_classification` / `edited_severity` / `edited_draft`. Four of the columns above have **no
writer at all**: `corrected_classification`, `corrected_severity`, `operator_added_source` and
`linked_incident_id`. That is not cosmetic — the read side had to be corrected for it:
`ingestion/learning.py` originally keyed its reclassification examples on
`corrected_classification` and therefore produced none, because the approve route writes
`edited_classification`. It now reads `edited_classification` (guard:
`test_learning_examples.py`). Only three `decision` values are ever written by a human —
`approve`, `approve_with_edits`, `reject` — plus `auto_approve` from the agent; `re_source`,
`link_umbrella` and `escalate` are enum members with no UI behind them.

**The single most valuable signal — re-sourcing — is designed but not yet captured.** When the
operator does due-diligence a machine couldn't (googles a dateless or weak item and finds the real
source), that produces a labeled `(weak_input → correct_source)` example. The column
(`operator_added_source`) and the consumer both exist —
`learning_monitor.rebuild_source_reputation()` counts a domain found that way as a
`re_source_win` **and** an approval, its strongest possible endorsement. What is missing is the
War Room control that writes it, so `re_source_wins` is currently always 0. The routing decision
still stands: dateless/low-confidence items go to War Room rather than being dropped (Q2 decision
= route-to-review), because **War Room is the sourcing-model's training-data generator, not just
an approval gate** — this is the one field that would make that literal.

### 2.2 The accumulator: `source_reputation`

Domains/sources earn or lose standing based on operator outcomes. Read back each run to weight
candidate confidence.

> **Status: the write side was missing until July 2026 — the loop was open.**
> `ingestion/learning.py` has always *read* this table and nudged candidate confidence by ±0.10
> from `trust_score`. **Nothing ever wrote it.** Every domain therefore resolved to the 0.500
> default, both thresholds (0.700 / 0.300) were unreachable, and the nudge was a permanent no-op.
> The loop drawn closed in §5 below was, in code, a straight line ending in a table nobody filled.
>
> `ops/learning_monitor.rebuild_source_reputation()` now supplies the write side, recomputing
> trust daily over a 365-day lookback of operator verdicts, with Laplace smoothing (Q-L2 resolved:
> simple ratio, smoothed):
>
> ```
> trust = (approvals + 1) / (approvals + rejections + 2)
> ```
>
> `approve` and `approve_with_edits` both count as approvals here — for *sourcing* purposes an
> edited draft still came from a domain worth citing. (The same module's other job, the
> agreement-rate snapshot in `learning_snapshots`, deliberately does **not** count an edit as
> agreement; see `AUTONOMY.md` §4.) It is a full recompute rather than incremental counters:
> idempotent, self-healing after a missed run, and immune to the double-count-on-retry bug that
> incremental counters invite. Agent auto-approvals (`decided_by='agent'`) are excluded —
> otherwise a domain could bootstrap its own reputation and then use that reputation to clear the
> confidence bar.
>
> **Smoothing alone was not enough, so there is also a sample floor.** A domain with 3 approvals
> and 0 rejections smooths to 0.800 and clears the +0.10 boost threshold on almost no evidence —
> and that nudge can push a 0.86 draft over the 0.95 auto-publish gate. So a domain stays pinned
> at the neutral 0.500 until it has at least `REPUTATION_MIN_OBSERVATIONS` (default **10**)
> operator verdicts; only then does the smoothed ratio apply. At the floor that means roughly 8 of
> the first 10 verdicts must be approvals to clear 0.700. Rejections are structurally scarcer than
> approvals right now (a rejected draft has no `incident_id` to trace a domain through), so early
> data skews positive by construction and neutral-until-proven is the conservative direction: the
> worst case is that the nudge keeps doing nothing, which is where the system has been all along.

```sql
-- Shipped verbatim in migration 006_phase1_apply_now.sql (RLS enabled, no
-- policy → service-role only). Rebuilt wholesale each day; never incremented.
CREATE TABLE source_reputation (
  source_domain      TEXT PRIMARY KEY,     -- e.g. 'mothership.sg', www- stripped
  approvals          INTEGER NOT NULL DEFAULT 0,
  rejections         INTEGER NOT NULL DEFAULT 0,
  re_source_wins     INTEGER NOT NULL DEFAULT 0,  -- times this domain was the operator's better
                                                  -- source. Always 0 today — see §2.1: nothing
                                                  -- writes operator_added_source yet.
  trust_score        DECIMAL(4,3) NOT NULL DEFAULT 0.500,  -- derived, 0..1
  last_updated       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### 2.3 How the Futurist agent reads it back (in-context)

Two things are loaded **once per pass**, at the start of `run_ingestion_pass()`:

1. `learning.load_source_reputation()` → `{domain: trust_score}`. Per candidate,
   `learning.apply_source_reputation()` returns a `(confidence_adjustment, flag)` pair that the
   orchestrator applies to whatever confidence Stage 2 produced: **+0.10** at `trust >= 0.700`,
   **−0.10** and a `low_reputation_source` flag at `trust <= 0.300`, nothing in between. A
   low-trust domain is deprioritised and flagged for the operator, **never silently dropped**.
   It is a nudge, not a gate — it clamps to 0.0–1.0 and touches no guardrail.
2. `learning.load_recent_signal_patterns()` → a block of **worked examples**, threaded into the
   Stage 2 input as `learning_context` and injected into both the classify call and the write
   call.

**Examples, not statistics — this was changed after the first version failed.** The read-back used
to emit aggregate counts ("operators re-classified 3 items from 'clown' to 'dagger'"). A frozen
model can do nothing with that: it says a correction happened but not *which kind of story* was
corrected, so there is nothing to pattern-match against. A labelled example carries the thing the
statistic threw away — the story. The block is therefore built from the 50 most recent signals as
up to 8 titled examples, reclassifications first (a correction is a sharper lesson than a
rejection), rejections round-robined across reject reasons rather than taken in recency order (a
third of live rejections are `duplicate`, and eight duplicate examples teach one lesson eight
times). Hard-bounded at 1400 characters because it goes into two Haiku calls per draft. Titles are
resolved by batched lookup through `queue_id` → `war_room_queue.proposed_title`, else
`incident_id` → `incidents.title`; an unresolvable row is skipped rather than rendered titleless.
Cold start returns `""`, which callers treat as "nothing to inject". Guard:
`test_learning_examples.py`.

**There is no umbrella/hub read-back.** The pass *does* propose related-incident links for human
confirmation, but they come from consolidation's live batched Haiku judgement against existing
incidents and queue rows (`consolidation/check.py`), not from accumulated learning signal — and
they are written as unconfirmed agent proposals (`incident_links` without
`confirmed_by_operator`). Recurring-figure detection likewise lives in the separate daily
`classifiers/pattern_detection.py` agent, which reads *published incidents*, not
`training_signals`. Feeding confirmed patterns back into the pass is unbuilt.

No weights change. The model is steered by what it's shown, not by what it is. **This is real
learning with an immediate effect and no training pipeline** — and it is the whole of Phase 1.

---

## 3. Phase 2 — Graduated autonomy (DESIGNED NOW, BUILT LATER)

Ties into the existing autonomy graduation tracker (TechSpec §13d). As agreement between agent
proposals and operator decisions climbs **within a category**, the agent earns higher autonomy
**for that category only** — never globally.

- Tracked per category. `classifiers/autonomy_tracker.py` exists and defines the thresholds
  (`min_samples` + `error_rate_max` per signal: `entity_dedup`, `classification`, `severity`, …),
  and `/autonomy/status` + `/autonomy/report` expose them. It is **reporting only** — nothing
  reads it to grant anything. Two things keep it from meaning much yet: the only route that writes
  `operator_changes.autonomy_signal` is the pattern-alert dismissal route, and every row it writes
  also carries `dismiss_reason_category`, which the tracker counts as a correction — so the
  measured error rate for those signals is structurally 1.0 and nothing can graduate. Fixing the
  instrumentation is Phase 2's first task, not an afterthought.
- Thresholds are human-set. Whether graduation itself must be human-granted is **no longer the
  settled answer it was**: the one shipped instance of earned autonomy — the oversized-cluster
  merge gate (`AUTONOMY.md` §5b) — lifts itself automatically once the grouper has ≥5 decisions on
  record at ≥0.80 smoothed trust, and re-arms itself on a single rejection. A gate with no exit is
  permanent homework; that was the reasoning, and it applies to Phase 2 as well.
- Regression is automatic and fast: if agreement drops on a graduated capability, autonomy is
  revoked back to review-required without ceremony. This is real in the oversized-merge gate today.
- **The per-category "never graduate" list is not in force.** §0's override made auto-publish a
  flat `confidence >= 0.95` with no classification carve-out, so crime and named-individual
  content already publish unattended when they clear the bar. Phase 2 would *reintroduce*
  per-category limits, not preserve them.

**Why deferred:** graduation requires a meaningful volume of correctly-labelled `training_signals`
to compute honest agreement rates. Building it before that data exists would be calibrating on
noise. The schema (Phase 1) is what makes Phase 2 possible later.

---

## 4. Phase 3 — Model fine-tuning / LoRA (ROADMAP ONLY — NOT BUILT)

When `training_signals` are voluminous and clean enough, a **human runs an offline fine-tune /
LoRA job**. (Earlier drafts cited TechSpec v1.5's "LoRA training confirmed
(yishunagain_v1.safetensors)" as precedent. That was the SDXL **image** LoRA for the art pipeline,
not a language-model fine-tune, and that pipeline was torn down in July 2026 — see
`docs/ART_PIPELINE.md`. Nothing here has been trained; this phase starts from zero.) Key
properties:

- **Deliberate, periodic, human-initiated.** Never a runtime self-update. The agent does not
  retrain itself; a person decides to, runs the job offline, evaluates the result, and chooses
  whether to deploy the new model version.
- **The training set is accumulating in the right shape** — Phase-1 `training_signals`, designed
  for exactly this. (This is "towards utopia" paying off: the simple early thing feeds the
  ambitious later thing.) The honest caveat is §2.1: the *decision* columns are populated on every
  route, but the *correction* columns — `operator_added_source`, `corrected_*`,
  `linked_incident_id` — have no writer, so the richest examples are being lost daily. That is
  instrumentation to fix now, not at fine-tune time; a column with no history cannot be
  backfilled.
- **Deploying a fine-tuned model is a versioned, reversible step**, gated like any deployment —
  not an autonomous act.

**Why roadmap-only:** building a self-retraining pipeline now would be premature over-engineering
on data that doesn't yet exist, and a runtime self-retrain would be the precise "AI drift"
failure mode this project exists to avoid. Phase 3 is named so the ambition is documented; it is
explicitly not in the build order.

---

## 5. The loop, end to end

```
        ┌─────────────────────────────────────────────────────────────┐
        │  FUTURIST AGENT — run_ingestion_pass() (daily, 14:58 SGT)    │
        │  reads source_reputation + recent operator examples          │
        │  → steers FROZEN model in-context (Phase 1)                  │
        └───────────────┬─────────────────────────────────────────────┘
                        │ proposes cards → war_room_queue
                        ▼
        ┌─────────────────────────────────────────────────────────────┐
        │  AUTO-PUBLISH GATE — ops/auto_publish.py                     │
        │  confidence >= 0.95 AND every guardrail clear → publishes    │
        │  anything else stays pending — never rejected                │
        └───────────────┬─────────────────────────────────────────────┘
                        │ published → training_signals (decided_by='agent')
                        │ held ─────▼
        ┌─────────────────────────────────────────────────────────────┐
        │  WAR ROOM — the human (PERMANENT, never removed)             │
        │  approve / approve-with-edits / reject / link / unpublish    │
        │  every held card, and any published card, forever            │
        └───────────────┬─────────────────────────────────────────────┘
                        │ each decision → training_signals
                        ▼
        ┌─────────────────────────────────────────────────────────────┐
        │  ACCUMULATED SIGNAL in Supabase  (Type-1 learning, automatic)│
        │  ops/learning_monitor.py (daily) rebuilds source_reputation  │
        │  from those decisions and writes a learning_snapshots delta  │
        │  read back next run  ──────────────────────────────────────▶│ (loop closes — Phase 1)
        │                                                             │
        │  ...and periodically, when rich enough...                   │
        │  human-run offline LoRA  ─────────▶ new model version       │ (Phase 3, deliberate)
        └─────────────────────────────────────────────────────────────┘

   Agreement rate rises → operator grants per-category autonomy (Phase 2)
   → human reviews LESS of the easy stuff, SAME authority on the hard stuff
   → ... that is the North Star.
```

**What writes to Supabase, and what does not:**
- **Writes to Supabase (automatic, safe):** training_signals, source_reputation,
  learning_snapshots, pipeline_state, pipeline_run_history, agent_runs / agent_events, queue rows,
  incidents. This is the agent "learning" in the Type-1 sense — accumulating data.
- **Does NOT happen automatically, ever:** the model's weights changing, the agent editing its own
  code, the agent widening its own permission set. Those are either human-gated (Phase 2/3) or
  never. The one place autonomy is *earned* without a human click — the oversized-merge gate — is
  a human-authored rule with a fixed ceiling and an automatic re-arm on rejection, not the agent
  writing itself a new permission.

---

## 6. Open questions for the owner

- **Q-L1 — RESOLVED, extended in place.** Migration `006_phase1_apply_now.sql` ALTERs the existing
  `training_signals` table (TechSpec §3.4) rather than creating a second one; `007` made
  `decision` NOT NULL; `011` added `decided_by`. One training-signal table, as recommended.
- **Q-L2 — RESOLVED, smoothed simple ratio, no decay.**
  `(approvals + 1) / (approvals + rejections + 2)`, recomputed daily over a 365-day lookback, with
  a 10-verdict floor before a domain moves off neutral (§2.2). Time decay was not added; the
  lookback window is the only recency control.
- **Q-L3 — ANSWERED, and answered the other way.** No category is permanently ineligible.
  Auto-publish is a flat `confidence >= 0.95` with no classification carve-out (§0 override), so
  `dagger` content and named individuals publish unattended when they clear the bar. If Phase 2 is
  ever built, this becomes a live question again.
- **Q-L4 — OPEN.** Nothing writes `operator_added_source`, so the re-sourcing signal §2.1 calls
  the most valuable one is never captured and `re_source_wins` is always 0. Does the War Room get
  a control for "I found the real source"?

---

## 7. Why this resists drift (design rationale)

- **The agent accumulates data, never modifies itself.** The one architectural rule that prevents
  the runaway-self-improvement failure mode. Drift can't compound through weights because weights
  never change at runtime.
- **The human loop is permanent and load-bearing.** It is the training-signal generator, not a
  removable scaffold. Cut it and learning stops — so it is never cut. Under the §0 override it is
  load-bearing in a second way: the operator's *unpublish* is the only correction the auto-publish
  gate ever receives, and `learning_snapshots.auto_publish_reverted` is what turns it into a
  calibration number.
- **Phased, with named deferrals.** Contextual now; graduated autonomy and fine-tuning are
  documented roadmap, explicitly not built. Ambition is recorded without inflating the build.
- **Ceilings on autonomy — but not the ones this section originally claimed.** The
  content-category ceiling (crime and named individuals never auto-publish) was removed by the §0
  operator override and is **not** in force. What still holds is a ceiling on *kind* of action:
  the hardcoded legal guardrails, which confidence cannot buy past; the per-run publish cap
  (`AUTO_PUBLISH_MAX_PER_RUN`, default 25); the rule that a failed gate leaves a card `pending`
  rather than rejecting it; and the fact that no agent changes weights, edits code, or widens its
  own permission set. The North Star is still reached by reducing review *volume* — but the bar on
  what matters is now held by the guardrails and the operator's editorial judgement, not by a
  category blocklist.
