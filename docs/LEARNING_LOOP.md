# Learning Loop — Design Document

**Status:** Proposed for review · **Targets:** TechSpec v1.9 (links §4.9, §13d) · **Companion to:** `INGESTION_DESIGN.md`
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
| **Futurist agent** | Forward (Jun 2026 →) | The daily live ingestion pass (`run_ingestion_pass()`, §4.9). SG MSM primary, Google News corroboration. **This is the agent the Learning Loop trains.** | **Yes — this is the learning subject.** |

The Learning Loop below is owned by the **Futurist agent**. The Historical agent contributes
signal (its proposed enrichments/links get human verdicts too) but is not itself "graduated."

---

## 2. Phase 1 — Contextual learning (BUILD NOW)

The agent improves **without its model weights changing.** Accumulated signal is read back into
prompts and scoring each run. Immediate effect, zero training infrastructure, frozen models
(Groq Llama for Stage 1, Claude for Stage 2). **Safe by construction: data in, behaviour steered,
no self-modification.**

### 2.1 What War Room records (the signal schema)

Every operator action becomes a structured `training_signal`. This extends the existing
`training_signals` table (TechSpec §3.4) — the shape is chosen so a future fine-tune (Phase 3)
can consume it directly.

```sql
-- Extends / aligns with TechSpec §3.4 training_signals.
-- One row per operator decision in War Room.
CREATE TABLE training_signals (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  -- WHAT the agent proposed (the input the model saw + its output)
  queue_id           UUID,                 -- war_room_queue row this decision came from
  source_url         TEXT,
  source_name        TEXT,
  source_type        TEXT,                 -- 'msm' | 'reddit' | 'edmw' | 'rss' ...
  proposed_classification TEXT,
  proposed_severity  INTEGER,
  agent_confidence   DECIMAL(3,2),

  -- WHAT the human decided (the label)
  decision           TEXT NOT NULL
                       CHECK (decision IN ('approve','reject','approve_with_edits',
                                           're_source','link_umbrella','escalate')),
  -- corrections (only populated when the human changed something)
  corrected_classification TEXT,
  corrected_severity INTEGER,
  operator_added_source    TEXT,           -- if human googled & found the real/better source
  linked_incident_id UUID,                 -- if human linked to an existing umbrella/hub
  reject_reason      TEXT,                 -- taxonomy (TechSpec §1.6 dismiss reasons)

  -- free-form learning note from the operator (the "why")
  operator_note      TEXT
);
```

**The single most valuable signal — re-sourcing.** When the operator does due-diligence a machine
couldn't (e.g. googles a dateless or weak item, finds the real source, records it via
`operator_added_source`), that produces a labeled `(weak_input → correct_source)` example. This is
*exactly* why dateless/low-confidence items route to War Room rather than being dropped (Q2
decision = route-to-review): **War Room is the sourcing-model's training-data generator, not just
an approval gate.**

### 2.2 The accumulator: `source_reputation`

Domains/sources earn or lose standing based on operator outcomes. Read back each run to weight
candidate confidence.

```sql
CREATE TABLE source_reputation (
  source_domain      TEXT PRIMARY KEY,     -- e.g. 'mothership.sg'
  approvals          INTEGER NOT NULL DEFAULT 0,
  rejections         INTEGER NOT NULL DEFAULT 0,
  re_source_wins     INTEGER NOT NULL DEFAULT 0,  -- times this domain was the operator's better source
  trust_score        DECIMAL(4,3) NOT NULL DEFAULT 0.500,  -- derived, 0..1
  last_updated       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### 2.3 How the Futurist agent reads it back (in-context)

At the start of each `run_ingestion_pass()`:
1. Load `source_reputation` → adjust candidate confidence (high-trust domains clear the bar more
   easily; repeatedly-rejected domains are deprioritised or auto-flagged).
2. Load recent `training_signals` patterns → inject a compact summary into the Stage 2 prompt
   ("operators recently rejected N 'property listing' items as off-topic; they consistently
   re-classify X as Y") so the **frozen** model behaves better *this run*.
3. Load confirmed umbrella/hub patterns → when a candidate matches a known pattern (cat-killer
   signature, a recurring named figure), propose the link for human confirmation.

No weights change. The model is steered by what it's shown, not by what it is. **This is real
learning with an immediate effect and no training pipeline** — and it is the whole of Phase 1.

---

## 3. Phase 2 — Graduated autonomy (DESIGNED NOW, BUILT LATER)

Ties into the existing autonomy graduation tracker (TechSpec §13d). As agreement between agent
proposals and operator decisions climbs **within a category**, the agent earns higher autonomy
**for that category only** — never globally.

- Tracked per category (classification × severity band). e.g. high-agreement on `custom`/CULTURE
  sev-1 might earn auto-publish; **crime and named-individual content NEVER graduate to
  auto-publish — permanent human review** (the §0 invariant, enforced).
- Thresholds are human-set and human-gated. Graduation is a privilege the operator grants, not an
  automatic escalation the agent awards itself.
- Regression is automatic and fast: if agreement drops on a graduated category, autonomy is
  revoked back to review-required without ceremony.

**Why deferred:** graduation requires a meaningful volume of `training_signals` to compute honest
agreement rates. Building it before that data exists would be calibrating on noise. The schema
(Phase 1) is what makes Phase 2 possible later.

---

## 4. Phase 3 — Model fine-tuning / LoRA (ROADMAP ONLY — NOT BUILT)

When `training_signals` are voluminous and clean enough, a **human runs an offline fine-tune /
LoRA job** (TechSpec v1.5 "LoRA training confirmed"). Key properties:

- **Deliberate, periodic, human-initiated.** Never a runtime self-update. The agent does not
  retrain itself; a person decides to, runs the job offline, evaluates the result, and chooses
  whether to deploy the new model version.
- **The training set already exists** — it's the Phase-1 `training_signals` accumulated in the
  shape designed for exactly this. No re-instrumentation needed. (This is "towards utopia" paying
  off: the simple early thing fed the ambitious later thing.)
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
        │  FUTURIST AGENT — run_ingestion_pass() (daily)               │
        │  reads source_reputation + training_signals patterns        │
        │  → steers FROZEN model in-context (Phase 1)                  │
        └───────────────┬─────────────────────────────────────────────┘
                        │ proposes cards → war_room_queue
                        ▼
        ┌─────────────────────────────────────────────────────────────┐
        │  WAR ROOM — the human (PERMANENT, never removed)            │
        │  approve / reject / re-source / link / correct              │
        │  every hard or named-individual item, forever               │
        └───────────────┬─────────────────────────────────────────────┘
                        │ each decision → training_signals (+ source_reputation)
                        ▼
        ┌─────────────────────────────────────────────────────────────┐
        │  ACCUMULATED SIGNAL in Supabase  (Type-1 learning, automatic)│
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
- **Writes to Supabase (automatic, safe):** training_signals, source_reputation, pipeline_state,
  queue rows, incidents. This is the agent "learning" in the Type-1 sense — accumulating data.
- **Does NOT happen automatically, ever:** the model's weights changing, the agent editing its own
  code, the agent granting itself autonomy. Those are either human-gated (Phase 2/3) or never.

---

## 6. Open questions for the owner

- **Q-L1.** Phase 1 signal capture — extend the existing `training_signals` table (TechSpec §3.4)
  in place, or create the richer schema above as a migration? (Recommendation: migrate/extend in
  place so there is one training-signal table, not two.)
- **Q-L2.** `source_reputation` trust formula — simple ratio (approvals / total) to start, or a
  time-decayed score? (Recommendation: simple ratio for Phase 1; add decay only if a domain's
  quality visibly changes over time.)
- **Q-L3.** Which categories are **permanently ineligible** for Phase-2 auto-publish? (Recommended
  hard-coded never-graduate set: anything `dagger`, anything naming a living individual, anything
  flagged developing. Confirm.)

---

## 7. Why this resists drift (design rationale)

- **The agent accumulates data, never modifies itself.** The one architectural rule that prevents
  the runaway-self-improvement failure mode. Drift can't compound through weights because weights
  never change at runtime.
- **The human loop is permanent and load-bearing.** It is the training-signal generator, not a
  removable scaffold. Cut it and learning stops — so it is never cut.
- **Phased, with named deferrals.** Contextual now; graduated autonomy and fine-tuning are
  documented roadmap, explicitly not built. Ambition is recorded without inflating the build.
- **Hard ceilings on autonomy.** Crime and named-individual content never auto-publish, at any
  maturity. The North Star is reached by reducing review *volume*, never by lowering the bar on
  what matters.
```
