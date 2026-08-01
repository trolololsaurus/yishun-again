# Image retry and War Room rectification

**Supersedes** the retry rules in `EDGE_CASES_AND_HARDENING.md` → B2
("IMAGE_MAX_RETRIES=1, never on a safety refusal") and the failure handling in
B5. Everything else in those files stands.

---

## 1. Why a naive retry does nothing

Gemini's safety filter is deterministic for a given prompt. Sending the same
text three times produces three identical refusals and three billed calls.

Retries are only worth having if **each attempt is materially different**. So
the ladder below softens the prompt at each step rather than repeating it.

---

## 2. Failure taxonomy — not everything retries

| Outcome | Retries | Ladder | Reaches War Room |
|---|---|---|---|
| **Suppressed** (guardrail #5) | 0 | — | **No** |
| **Safety refusal** | up to 3 | escalating softening | Yes, if all fail |
| **Transient** (network, timeout, 5xx) | 2, same prompt | — | Yes, if all fail |
| **Validation** (corrupt bytes, wrong aspect) | 1, same prompt | — | Yes, if it fails |

**Suppression is not a failure.** It is the intended outcome, it must never
enter the retry path, and it must never appear in the rectification queue —
otherwise the queue becomes a prompt to override the guardrail. Mark those
incidents `image_suppressed` and leave them alone.

---

## 3. The softening ladder

Each rung is a fresh Haiku rewrite of the scene paragraph only. Style preamble,
palette and exclusions are unchanged — the template still wraps every attempt.

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
one is available (`finish_reason`, `block_reason`, safety category). Where it is
not, fall back to the generic instruction above.

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

**Consequence for B5.** Rectification is by definition post-insert, so it
requires the update-after-insert path that was removed from the automatic flow.
That is fine — it is a manual operator action, not a pipeline step — but it
**must trigger a revalidation hook** after writing `pixel_art_url`, or the live
page keeps serving the placeholder under ISR despite a correct row. Build the
revalidation call as part of the rectify endpoint, not as an afterthought.

---

## 5. Cost ceiling counts attempts, not incidents

Three attempts per incident triples the worst case. `IMAGE_MAX_PER_RUN=25`
counts incidents and would permit 75 calls.

Add **`IMAGE_MAX_ATTEMPTS_PER_RUN`** (default 40). When reached, stop generating
for the remainder of the pass, publish the rest with null, flag them for
rectification, and emit a warning. Publication continues regardless.

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
    { url: str|None, status: 'ok'|'suppressed'|'refused'|'transient'|'invalid',
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
> cohort: migration 014 backfills every pre-existing incident to it, and both
> writers use it whenever art generation is off or unconfigured. Those were never
> attempted at all — a backfill job, not a per-incident operator decision.
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

Per pass, into `agent_events`:

- attempts issued, split by outcome
- refusal rate **by classification** — if daggers refuse at 60% and clowns at
  2%, that is a product finding about your archive, not a bug
- ladder rung that succeeded, when one did — if rung 3 carries most daggers,
  environment-only is your real dagger art direction and the ladder has told
  you something the design could not
- count entering rectification per pass

If the rectification queue grows faster than you clear it, the ladder is not
working and the answer is a different art direction for that classification —
not more retries.
