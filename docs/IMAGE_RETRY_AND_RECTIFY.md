# Image retry and War Room rectification

**Supersedes** the retry rules in `EDGE_CASES_AND_HARDENING.md` §3 → B2
("IMAGE_MAX_RETRIES=1, never on a safety refusal") and the failure handling in
§3 → B5. Everything else in those files stands.

**This is built, not planned.** The prompt blocks in §6 and §7 are the brief that
produced the code; read them as the contract, not as outstanding work. Where the
implementation settled somewhere slightly different from the brief, the text
below says so. What exists:

| Piece | Where |
|---|---|
| Ladder, per-outcome caps, attempt budget, result contract | `packages/agents/art/generate_image.py` |
| Operator single-shot render (no Haiku, no ladder, no budget) | `render_prompt()`, same file |
| HTTP bridge for the TypeScript caller | `POST /art/generate`, `POST /art/rectify` in `packages/agents/main.py` |
| Status columns + CHECK | migrations `014_image_status.sql`, `015_image_status_check.sql` |
| Rectification queue | `apps/war-room/app/rectify/page.tsx`, `components/RectifyCard.tsx` |
| Operator actions | `app/api/incidents/[id]/rectify/route.ts`, `.../no-image/route.ts` |
| Status vocabulary — one declaration per layer | `apps/war-room/lib/types.ts` (`ImageStatus`, `RECTIFIABLE_STATUSES`), `ImageResult` in the generator, the 015 CHECK |
| Guards | `packages/agents/test_image_generation.py`, `test_rectify_guards.py` |

---

## 1. Why a naive retry does nothing

Gemini's safety filter is deterministic for a given prompt. Sending the same
text three times produces three identical refusals and three billed calls.

Retries are only worth having if **each attempt is materially different**. So
the ladder below softens the prompt at each step rather than repeating it.

---

## 2. Failure taxonomy — not everything retries

| Outcome | Attempts | Ladder | Reaches War Room |
|---|---|---|---|
| **Suppressed** (guardrail #5) | 0 | — | **No** |
| **Safety refusal** | up to 3 | escalating softening | Yes, if all fail |
| **Transient** (network, timeout, 5xx, scene writer) | up to 2, backoff between | — | Yes, if both fail |
| **Validation** (corrupt bytes, wrong aspect) | up to 2 (one retry) | — | Yes, if both fail |
| **Ceiling reached** mid-pass | 0 | — | Yes, as `skipped` |

The caps are named constants in `generate_image.py` — `REFUSAL_MAX_ATTEMPTS=3`,
`TRANSIENT_MAX_ATTEMPTS=2`, `VALIDATION_MAX_ATTEMPTS=1` (a retry count, so two
attempts) — never a `while not ok:` loop. They are counted per outcome class, so
a refusal followed by two transients ends `transient`, not `refused`.

"Same prompt" is not literal on the non-refusal paths. Only the *rung* is held
constant: the loop re-runs the Haiku scene writer on every attempt, so a
transient or validation retry gets a freshly written scene at the same softening
level rather than a byte-identical prompt. The refusal path is the only one that
advances a rung.

**Suppression is not a failure.** It is the intended outcome, it must never
enter the retry path, and it must never appear in the rectification queue —
otherwise the queue becomes a prompt to override the guardrail. Mark those
incidents `suppressed` (`incidents.image_status`) and leave them alone.

---

## 3. The softening ladder

Each rung is a fresh Haiku rewrite of the scene paragraph only. The five
template constants — style preamble, composition, physical coherence, palette,
exclusions (`art/prompt_template.py::assemble_prompt`) — are unchanged, so the
template still wraps every attempt.

```
Attempt 1  As written. Full scene per ART_PIPELINE.md §3.

Attempt 2  Haiku rewrites, instructed to remove what likely triggered the
           refusal — visible injury, blood, weapons, bodies, physical violence,
           acute distress — while keeping setting, time of day, mood and the
           non-violent props. Aftermath rather than act.

Attempt 3  Environment only. No human figures at all. The setting, the light,
           the atmosphere, and any neutral objects. No incident depicted.
```

Pass the refusal reason from the API response into the attempt-2 rewrite where
one is available (`finish_reason`, `block_reason`, safety category — collected by
`_refusal_reason()`). Where it is not, fall back to the generic instruction above.

Attempt 3 is deliberately close to the register I would have recommended for
serious incidents anyway: an establishing shot, not a depiction. On a dagger
card that is very often the better image, not merely the permitted one.

---

## 4. Publication never blocks on the image

If the ladder exhausts, the incident **publishes with `pixel_art_url = null`**
and is flagged for rectification. It does not sit unpublished waiting for a
picture.

Rationale: the whole failure class disproportionately hits DARK EVENTS, which
are your most newsworthy cards. Under unattended operation, blocking on image
failure would silently withhold exactly the stories that matter most. The
frontend already degrades to the placeholder and `og-default.jpg`.

Both writers behave this way: the War Room approve route calls
`generateIncidentArt` before its INSERT and inserts regardless of the outcome,
and `ops/auto_publish.py::_generate_art` returns the status fields to merge and
never raises. A failure or a suppression writes `pixel_art_url: null` plus the
status, and the row publishes.

**Consequence for B5.** Rectification is by definition post-insert, so it
requires the update-after-insert path that was removed from the automatic flow.
That is fine — it is a manual operator action, not a pipeline step — but it
**must trigger a revalidation hook** after writing `pixel_art_url`, or the live
page keeps serving the placeholder under ISR despite a correct row.

As built: `apps/web/app/incidents/[slug]/page.tsx` sets `revalidate = 3600`, so
the stale window is up to an hour. The rectify route **awaits**
`revalidateIncident(slug)` (`apps/war-room/lib/revalidate.ts`) — an unawaited
fetch is frozen when the serverless function returns — and reports the outcome
back as `revalidated` / `revalidate_reason` rather than swallowing it. A failed
hook is never fatal, because the row is already correct, but the card tells the
operator the page will keep serving the placeholder.

---

## 5. Cost ceiling counts attempts, not incidents

Three attempts per incident triples the worst case. The `IMAGE_MAX_PER_RUN=25`
proposed in `EDGE_CASES_AND_HARDENING.md` §3 → B2 counts incidents and would
permit 75 calls.

**`IMAGE_MAX_ATTEMPTS_PER_RUN`** (default 40) replaced it and is the only image
ceiling in the code — `IMAGE_MAX_PER_RUN` was never implemented. When reached,
generation stops for the remainder of the pass, the rest publish with null and
status `skipped`, and the outcome is flagged for rectification. Publication
continues regardless.

Two details the name does not carry:

- The budget is an explicit `AttemptBudget` object, not module state, so a pass
  owns its own count and tests cannot leak counts into one another.
  `ops/auto_publish.py` builds exactly one per pass and threads it through every
  publish. A `/art/generate` call from the War Room builds its own, because that
  is one operator click rather than an unattended loop — the ceiling bounds the
  autonomous pass, not the operator.
- It counts loop attempts, not billed image calls. An attempt whose Haiku scene
  writer failed before any image call was made still spends budget. That is the
  conservative direction: the ceiling exists to bound a runaway pass, and a
  runaway scene writer costs money too.

---

## 6. Replacement text — append to prompt B2

```
RETRY LADDER — replaces the earlier "IMAGE_MAX_RETRIES=1, never on refusal".

Classify every failure before deciding whether to retry:

  suppressed  -> return None immediately. 0 attempts, 0 Haiku calls,
                 0 image calls. Mark image_suppressed. NEVER retry, and never
                 route to rectification.
  refusal     -> up to IMAGE_MAX_ATTEMPTS=3, using the softening ladder below.
  transient   -> up to 2 attempts, same prompt, exponential backoff.
  validation  -> 1 attempt, same prompt. (corrupt bytes / wrong aspect)

SOFTENING LADDER (refusals only). Each rung is a fresh Haiku rewrite of the
SCENE PARAGRAPH ONLY. Style preamble, palette and exclusions are template
constants and never change between attempts.

  1  the scene as written
  2  rewritten to remove likely triggers — visible injury, blood, weapons,
     bodies, physical violence, acute distress — keeping setting, time of day,
     mood and non-violent props. Aftermath, not act.
  3  environment only. No human figures. Setting, light, atmosphere, neutral
     objects. No incident depicted.

Pass the API's refusal reason (finish_reason / block_reason / safety category)
into the attempt-2 rewrite when present. Generic instruction when absent.

RETURN CONTRACT on exhaustion:
  Return a structured result, not a bare None, so the caller can route it:
    { url: str|None,
      status: 'ok'|'suppressed'|'refused'|'transient'|'invalid'|'skipped',
      attempts: [ {n, prompt, outcome, reason} ], final_prompt: str }
  The attempts list is what the operator sees in War Room — they need to know
  what was tried and what was refused, not just that it failed.

CEILING:
  IMAGE_MAX_ATTEMPTS_PER_RUN=40 counts ATTEMPTS across the pass, not incidents.
  Reached -> stop generating, publish remaining incidents with null, flag for
  rectification, emit an agent_events warning. Publication is never blocked.

Tests: refusal on attempts 1 and 2 then success on 3 returns ok with 3 attempts
recorded; three refusals returns status 'refused' with all three prompts;
suppressed makes zero calls and is never marked refused; attempt ceiling stops
generation but does not stop publication.
```

> **Three deltas between this brief and `art/generate_image.py`.** None changes
> the contract, but do not read the block above more literally than the code
> supports:
>
> - **There is no `image_suppressed`.** Suppression returns an `ImageResult` with
>   `status='suppressed'` — never a bare `None`, which is the whole point of the
>   structured return — and that string is the value written to
>   `incidents.image_status`. Since 015 the CHECK constraint would reject
>   `image_suppressed` outright.
> - `IMAGE_MAX_ATTEMPTS=3` is spelled `REFUSAL_MAX_ATTEMPTS` in the code, and
>   `VALIDATION_MAX_ATTEMPTS=1` is a *retry* count — a validation failure is
>   retried once, for two attempts. §2 has the exact caps.
> - "same prompt" on the transient and validation paths means the same ladder
>   rung, not identical bytes: the scene writer runs again on every attempt.
>
> The first three test lines are asserted directly in `test_image_generation.py`;
> the fourth is covered as `status='skipped'` with zero calls, its publication
> half being `ops/auto_publish.py`, which merges the result into the insert and
> never blocks on it. The same file also guards the R2 HEAD verification after
> the PUT, the `?v=` content hash changing only when the bytes do, and a wrong
> aspect ratio coming back `invalid` rather than salvaged by squashing.

---

## 7. New prompt — B4b: War Room rectification

Run after B4 and B5.

```
Goal: give the operator a way to rectify incidents whose image generation
failed, without blocking publication.

STATE:
Incidents need a distinguishable image status, not a bare null. Add a column or
raw_content field with values:
  ok | suppressed | refused | transient | invalid | skipped | pending | no_image_final
Expand/contract: add nullable, backfill existing rows to 'pending', enforce
later. Write the numbered migration and STOP — do not apply it.

'suppressed' is terminal and must NEVER appear in the rectification queue.
Guardrail #5 is not operator-overridable. Do not build an override control.

> **As built.** `014_image_status.sql` adds three nullable columns —
> `image_status`, `image_prompt`, `image_attempts` — not a `raw_content` field,
> plus `idx_incidents_image_status` over the four rectifiable values. The
> backfill is conditional, which is narrower than "existing rows to 'pending'":
> a pre-existing row becomes `pending` only if `pixel_art_url IS NULL`,
> otherwise `ok`.
>
> The "enforce later" half is `015_image_status_check.sql`: it looks for
> unknown values first, then adds `incidents_image_status_check` `NOT VALID` and
> validates it. NULL stays legal. Without it the database was the only layer of
> the three that agreed on this vocabulary (TS union, Python docstring, column
> comment) and did not enforce it — and a bad value that merely *looks* terminal
> is unreachable by every code path that would fix it.
>
> Both are hand-applied in the Supabase SQL Editor; there is no migration runner
> (QA M15).

WAR ROOM VIEW:
A filtered list of published incidents with status in (refused, transient,
invalid, **skipped**). For each, show:

> **`skipped` was missing from this list as originally written.** It is the
> status set when `IMAGE_MAX_ATTEMPTS_PER_RUN` is reached mid-pass — an incident
> the pipeline never even tried, purely because the budget ran out. That is
> precisely a case for a human, so it belongs in the queue. The implementation
> and `idx_incidents_image_status` both use the four.
>
> **`pending` is deliberately NOT in the queue**, and it is the largest imageless
> cohort: migration 014 backfills every pre-existing incident without an image to
> it, and both writers use it whenever art generation is off or unconfigured
> (`ART_GENERATION_ENABLED` unset on the agents side, `AGENTS_API_URL` or
> `OPS_TOKEN` unset on the War Room side). Those were never attempted at all — a
> backfill job, not a per-incident operator decision.
  - the incident title and classification
  - every attempted prompt with its outcome and refusal reason
  - the last attempt's prompt, pre-loaded into an editable field

OPERATOR ACTIONS — four, no more:
  1. Edit prompt and retry     — runs the image call with the edited prompt,
                                 single attempt, no ladder (the operator has
                                 already made the judgement)
  2. Retry as-is               — re-runs the last attempt unchanged, for
                                 transient failures
  3. Publish without image     — sets status to 'no_image_final'. Terminal.
                                 Backfill jobs must skip it.
  4. Leave pending             — no change, stays in the queue

> **As built** (`components/RectifyCard.tsx` and the two routes). Actions 1 and
> 2 are one endpoint — `POST /api/incidents/[id]/rectify` — because they differ
> only in where the prompt comes from: a body with `prompt` uses it, a body
> without falls back to the stored `image_prompt`. Action 4 is client-side only:
> it drops the card from this session's list and writes nothing, so the row is
> back on the next load.
>
> Guardrail #5 is enforced at three independent layers, not just by the queue
> filter: the page selects `.in('image_status', RECTIFIABLE_STATUSES)` so
> suppressed rows are excluded by construction; both routes re-check server-side
> and answer 422 (rectify) or 409 (no-image); and `render_prompt` runs the
> deterministic `suppress_image()` gate itself before spending anything, when the
> caller passes `incident` — which the rectify route always does. There is
> deliberately no control anywhere that can set or clear `suppressed`.
> `test_rectify_guards.py` reads the TypeScript as text and asserts exactly that.
>
> Failures are persisted too, and the write is checked: a refusal writes the new
> `image_status`, `image_prompt` and appended `image_attempts` before answering,
> so the reason the operator reads off the screen survives a reload. Both writes
> are compare-and-set on `RECTIFIABLE_STATUSES` (409 on a miss), so two operators
> on one row — or a suppression landing in between — cannot both win. Attempt
> history is appended and renumbered, capped at the most recent 10, because it is
> JSONB on a published row that `/rectify` loads 200 of at a time.

On successful rectification:
  - upload to R2, write pixel_art_url, set status 'ok'
  - TRIGGER A REVALIDATION HOOK for the incident page. Without it the live page
    keeps serving the placeholder under ISR despite a correct row. This is the
    one place the update-after-insert path is legitimate, so the revalidation
    is mandatory, not optional.

TERMINAL STATES that future backfill jobs must never retry:
  suppressed, no_image_final
Anything else spread across passes is an infinite retry loop, just slower.

The manual retry path is exempt from IMAGE_MAX_ATTEMPTS_PER_RUN — it is
operator-initiated, one at a time, not a pipeline loop.

Verify: tsc --noEmit clean on war-room; full Python suite; paste both. Then
demonstrate one rectification end-to-end and paste the resulting incidents row
plus the live page URL showing the image.
```

---

## 8. Telemetry to add

**Not built yet.** What exists today is per-incident, not per-pass:
`ops/auto_publish.py::_generate_art` writes one `agent_events` warning per row
whose status is neither `ok` nor `suppressed` — `image_refused`,
`image_transient`, `image_invalid`, `image_skipped`, or `image_failed` when the
generator itself blew up — carrying the slug and the last attempt's reason,
truncated to 200 characters. Nothing aggregates those, and the War Room approve
path emits no event at all; its history lives only in the row's `image_attempts`.

Still worth adding, per pass, into `agent_events`:

- attempts issued, split by outcome
- refusal rate **by classification** — if daggers refuse at 60% and clowns at
  2%, that is a product finding about your archive, not a bug
- ladder rung that succeeded, when one did — if rung 3 carries most daggers,
  environment-only is your real dagger art direction and the ladder has told
  you something the design could not
- count entering rectification per pass

The raw material is already stored: `attempts[].outcome` and the rung ordering
are on every incident row, so this is an aggregation, not new instrumentation.

If the rectification queue grows faster than you clear it, the ladder is not
working and the answer is a different art direction for that classification —
not more retries.
