# Edge cases and hardening — addendum to CLAUDE_CODE_PROMPTS_v2

Read §1 first. Those are defects that break behaviour rather than merely harden
it: §1.1 and §1.2 in the spec as written, §1.3 in what shipped from it. §2
onward are per-prompt additions: append each block to the named prompt.

---

# 1. Three defects — fix before building

## 1.1 Batch API and generate-before-insert are mutually exclusive

`ART_PIPELINE.md` §1 says use batch mode (50% off, "latency is free because
generation is post-approval"). §6.1 says generate synchronously before insert so
the URL is in the INSERT.

**Both cannot be true.** Gemini's Batch API has up to 24-hour turnaround. You
cannot block an operator's approve click on a batch job, and you cannot insert a
URL that will not exist until tomorrow.

**Resolution — two paths, explicitly:**

| Path | Tier | Cost | Why |
|---|---|---|---|
| Operator approve, auto-publish | **Standard** | $0.0336 | Synchronous, blocks insert, seconds not hours |
| Bulk archive backfill | **Batch** | $0.0168 | One-off, no user waiting, tolerates 24h |

**Resolved (2026-08-24).** `IMAGE_USE_BATCH` has been removed from `.env.example`
— nothing in `packages/agents/` ever read it and `art/generate_image.py` has no
batch branch. The interactive path is standard-tier only. At ~5 images/day the
premium is ~$2.50/month — irrelevant.

## 1.2 The suppression gate depends on a model-generated field

`suppress_image()` as specified in `ART_PIPELINE.md` §4 and Track B's B1 reads
only `incident["tags"]`. Tags are produced by the Haiku classifier. If it does
not emit a `suicide` tag on a suicide story — and it sometimes will not — the
gate never fires and the image renders.

For the one check that must not fail, depending on a model output is the wrong
architecture. Everything else in this programme moved *toward* deterministic
verification; this went the other way.

**Fix — second condition, OR not AND.** Shipped in
`packages/agents/art/suppression.py`:

```python
SUPPRESS_TAGS = frozenset({"suicide", "self-harm"})
SUPPRESS_PHRASES = (
    "suicide", "self-harm", "self harm",
    "took his own life", "took her own life", "took their own life",
)

def suppress_image(incident: dict) -> bool:
    try:
        if not isinstance(incident, dict):
            return True                        # fail closed — §2 rule 4
        if _normalise_tags(incident.get("tags")) & SUPPRESS_TAGS:
            return True
        text = _incident_text(incident)        # lowercased title + summary
        return any(p in text for p in SUPPRESS_PHRASES)
    except Exception:
        return True                            # a gate that raises did not run
```

Deterministic, no model call, and it fires on the Blk 737 card whether or not
the classifier tagged it. Three things the original sketch left out, all of
them stricter rather than looser:

- **Fails closed.** An input the gate cannot read — `None`, a bare string, a
  mapping whose `.get()` raises — returns `True`. The sketch would have raised
  instead, and a gate that raises is a gate that did not run.
- **Defensive tag normalisation.** `_normalise_tags` tolerates `None`, a bare
  string in place of a list, and non-string members; it folds whitespace and
  underscores to hyphens so `self harm` and `self_harm` both match the
  canonical `self-harm` tag.
- **Substring, not word-boundary, matching.** Deliberate: over-suppression
  costs a placeholder, under-suppression puts a generated picture on a suicide
  story.

Deliberately narrow in the other direction — severity, death count and
classifier confidence are **not** consulted. Fatalities, violence, fires and
severity-5 incidents all generate normally.

`ART_PIPELINE.md` §4 and B1 are amended accordingly. Guard:
`test_image_suppression.py`, including the case that is the whole point of the
amendment — no suicide tag, "suicide" in the summary, detected.

**Detector vs policy (2026-08-09).** `suppress_image()` above is now only the
DETECTOR — its name is historical; read it as "is this a guardrail-#5 incident".
What happens on a hit is a separate, switchable POLICY
(`SENSITIVE_INCIDENT_ART`, `art/sensitive_scene.py`): by default a detected
incident renders a fixed, non-graphic police-response tableau (a shut blue
privacy tent, SPF officers, tape, patrol car — fully deterministic, never the
body/act/method, `scene_is_clean()`-screened, falling back to no image if it
can't be produced safely or the model refuses), and `SENSITIVE_INCIDENT_ART=suppress`
restores the original no-image behaviour. Keeping the detector deterministic and
total still matters exactly as much: the policy branch only ever runs on what the
detector flags. See `ART_PIPELINE.md` §4b and `test_sensitive_art.py`.

## 1.3 Guardrail #4 was unreachable when the model returned a null category

Legal guardrail #4 — political content forces `confidence = 0` and an
operator-visible reject marker — sat **below** the classification / severity /
confidence coercion in `filters/stage2_writer.py::_classify`. That coercion ran
`result["classification"].lower()`, which raises `AttributeError` on
`"classification": null` — and null is exactly what the model tends to return
on a political story, because the prompt tells it to reject rather than
categorise.

So for a subset of the very content the guardrail exists to catch, the
candidate died on an exception before the guardrail was ever read: no
`confidence = 0`, no `[POLITICAL CONTENT DETECTED — REJECT]` marker, no
operator email, no `agent_events` warning row. Observed live on 2026-08-02 on
an MP-resignation article.

A silent crash is worse than the silently-zeroed row that the 2026-07-30
alerting was added to fix — the zeroed row at least reaches the queue and can
be seen.

**Fix — evaluate the guardrail before anything that can raise:**

1. Read `political` first and force `confidence = 0.0` there and then.
2. Coerce the category defensively (`isinstance(x, str)`), never `.lower()` on
   an unvalidated field.
3. An invalid category on a **non**-political row still raises `ValueError`.
   That is a genuine model failure and must stay loud.
4. On a political row, substitute a valid placeholder category (`dagger`) so
   the guardrail's own reject path can complete and alert. The row is rejected
   on confidence, not on category, and the column is NOT NULL downstream.

**The general rule: a guardrail must be evaluated before any validation that
can raise.** A check placed after validation only ever runs on well-formed
input, and malformed input is exactly what a guardrail is for.

Guard: `test_stage2_guardrails.py` — political with a null category, political
with an invalid category, and non-political with a null category still raising.

---

# 2. Cross-cutting rules — add to the standing preamble

```
FAILURE-MODE RULES — apply to every task in this programme:

1. BOUNDED RETRIES. Every retry loop has an explicit max attempt count as a
   named constant. No `while not valid:` without a counter. An exhausted retry
   budget is a logged failure, never another attempt.

2. NO UNBOUNDED FAN-OUT. Any loop issuing API calls has a hard per-pass ceiling
   as a named constant, in the same shape as AUTO_PUBLISH_MAX_PER_RUN=25. A bug
   upstream that produces 500 candidates must not produce 500 API calls.

3. TIMEOUTS ON EVERY EXTERNAL CALL. Model calls, R2, HTTP. No call may block
   indefinitely. A timeout is a failure, not a hang.

4. FAIL CLOSED, NOT OPEN. When a check cannot complete, the safe answer is the
   restrictive one: do not merge, do not publish, do not generate. Never treat
   "I could not verify" as "verified".

5. PERMANENT FAILURE MARKERS. If an item fails a step, record that it failed so
   the next pass does not retry it forever. A retry loop across passes is still
   an infinite loop, just slower and more expensive.

6. NO POSITIONAL REFERENCES ACROSS A MODEL BOUNDARY. If you send a numbered
   list to a model and act on returned numbers, you are one reordering away from
   acting on the wrong record. Send stable IDs and match on ID.
```

---

# 3. Per-prompt additions

## → A2 (prompt caching)

**Not implemented — measured and rejected** (commit `d976d7b`). The
consolidation pool is filtered and ranked per candidate, so there is no
byte-identical prefix to cache, and the filtered prompt (3,889 tokens) is below
Haiku 4.5's 4,096-token minimum cacheable prefix. The edge cases below are kept
because they are the reasons the measurement was worth taking.

```
EDGE CASES — implement all:

- Caching is a NET LOSS on small passes. A cache write costs more than plain
  input, so a pass with only one or two candidates pays extra for nothing. Add
  CACHE_MIN_CANDIDATES (default 3): below it, skip caching entirely.

- The cached block must be byte-identical or you silently pay full price with
  no error. Sort the pool deterministically by a stable key. Never interpolate
  timestamps, "generated at", run IDs, or per-candidate data into the cached
  section. Assert byte-equality of the cached block across calls in a pass and
  log a warning on mismatch — a silent cache miss is a silent cost regression.

- Cache TTL is ~5 minutes. Some passes have run for many minutes (the changelog
  records a 909s pass). If the gap between calls exceeds TTL the cache expires
  mid-pass. Log cache hit/miss per call so this is visible rather than inferred
  from the bill.

- DB row ordering is not guaranteed without ORDER BY. Add an explicit ORDER BY
  to the pool query or the prefix will differ between passes.

Report: cache hit rate across a full pass, and the pass duration.
```

## → A3 (batched judging) — highest-risk change in the programme

```
EDGE CASES — implement all. This task can corrupt data if done loosely.

CORRUPTION RISK — the one that matters most:
- Consolidation returns "which archive item matches". If you send a numbered
  list and the list is rebuilt or reordered between prompt construction and
  result handling, you will merge a candidate into the WRONG incident. That is
  silent data corruption in the published archive.
  -> Send stable incident IDs in the prompt, require the model to return an ID,
     and validate the returned ID exists in the exact set you offered. An ID
     not in the offered set is treated as null (= new incident), never as a
     match. Do NOT use positional indices for consolidation.

PROMPT SIZE:
- Batching is unbounded by construction. A pass with 80 keyword-linked
  candidates would build one enormous prompt and may exceed the context window.
  Add GROUPER_MAX_BATCH (default 25). Above it, chunk.
- Document the consequence: groups CANNOT span chunks. Chunking trades a small
  recall loss for bounded prompts. Log every time chunking occurs so the
  frequency is known.

RESPONSE VALIDATION — all of these degrade to all-singletons, never to a merge:
- non-JSON or unparseable response
- any index outside [0, N)
- any index appearing in more than one group
- any input candidate missing from the returned groups entirely
  (do NOT silently drop unreferenced candidates — they become singletons)
- a single group containing every candidate (near-certain model failure)

RETRIES:
- At most ONE retry on unparseable JSON. Named constant GROUPER_MAX_RETRIES=1.
  After that, fall back to keyword-only clustering. Never loop.

TIMEOUT:
- Hard timeout on the grouper call. Timeout = fallback to keyword-only, not a
  hang and not a retry storm.

Add tests for every bullet above. Paste the full suite output.
```

## → A4 (locality veto)

```
EDGE CASES:

- The veto must be conservative in the SAFE direction. Different numbers ->
  refuse to merge. But "Blk 512" vs "512C" vs "Block 512C" must normalise to
  the same token, or you will split genuine clusters. Normalise case, strip
  "block"/"blk", and treat a bare number and the same number with a letter
  suffix as DIFFERENT (512 and 512C are different buildings).

- One side empty is NOT a veto. Many articles omit the block. Vetoing on
  absence would break most legitimate clusters.

- An article citing MULTIPLE block numbers (a police sweep across several
  blocks) must not veto against everything. If either side has more than two
  distinct locality tokens, skip the veto for that pair and log it.

Test each of these explicitly.
```

## → A6 (groundedness check) — this one can double your bill

```
EDGE CASES:

- REGENERATE EXACTLY ONCE. Named constant GROUNDEDNESS_MAX_RETRIES=1. On the
  second failure, flag and return. Never loop. An unbounded regenerate loop on
  an over-strict checker burns tokens until the cost guard trips.

- CIRCUIT BREAKER. If more than GROUNDEDNESS_FAIL_RATE_ABORT (default 0.30) of
  drafts in a pass fail the check, the checker is wrong, not the model. Disable
  it for the remainder of the pass, emit an agent_events warning, and continue
  without it. Otherwise a bad regex doubles the cost of every draft in every
  pass, silently.

- FALSE-POSITIVE SOURCES to handle before shipping:
  * abbreviation drift — "Yishun Street 81" in summary vs "Yishun St 81" in
    source. Normalise common abbreviations before comparing.
  * number formatting — "8.20pm" vs "8:20pm", "1,200" vs "1200", "two" vs "2".
  * possessives and plurals on proper nouns.
  * sentence-initial capitalisation producing false "proper nouns".
  Compare case-insensitively on a normalised form, not raw substring.

- The checker must NEVER raise out of write_stage2. Wrap it. A checker
  exception degrades to "flag", not to "pass" and not to a crash.

Report the observed failure rate across the 30-item eval set before enabling
the retry.
```

## → A8 (deaths validation)

```
EDGE CASES — this regex will produce false positives and stall the pipeline:

- "died down" is the obvious trap: "the flames died down" contains "died".
  Require word boundaries AND exclude known non-fatal collocations
  ("died down", "died away", "dying embers").
- Quoted speech and hypotheticals: "police feared he had died", "could have
  died", "if he had died". Negation and modality must be handled or you will
  block publication on stories with no fatality.
- Past-tense reporting of OTHER incidents in the same article ("the third such
  death this year") must not be counted as this incident's death.

- STALL RISK: if the validator blocks auto-publish too readily, incidents
  accumulate in the queue with nobody watching. Emit an agent_events warning on
  every block, and report the block rate in the daily summary. A block rate
  above 20% means the validator is wrong.

- Ambiguity FLAGS, it never corrects. Never overwrite the model's number.
```

## → B1 (suppression gate)

**Shipped** as `art/suppression.py` — §1.2 above is the current gate, guarded by
`test_image_suppression.py`.

```
Implement the amended version from EDGE_CASES §1.2 — tag check OR deterministic
phrase check on title + summary. Do not ship the tag-only version.

EDGE CASES:
- tags may be None, a string instead of a list, or contain non-strings.
  Normalise defensively. Never raise.
- title or summary may be None. Handle without raising.
- The function must be total: any input returns True or False, never an
  exception. A crash here means the gate did not run.

Add a test asserting that an incident with NO suicide tag but the word
"suicide" in its summary IS suppressed. That is the Blk 737 case and it is the
whole point of the amendment.
```

## → B2 (image generation) — most new failure surface

**Superseded in part by `docs/IMAGE_RETRY_AND_RECTIFY.md`** (§2, §5, §6), which
was written after this block and explicitly replaces its retry rules. Three
constants below are not what shipped in `art/generate_image.py`:

| Below | Shipped | Why |
|---|---|---|
| `IMAGE_MAX_RETRIES=1`, never on a refusal | per-outcome caps — refusal 3 (each rung a softened rewrite), transient 2, validation 1, suppression 0 | The safety filter is deterministic: resending the same prompt buys identical refusals and identical bills. A retry is only worth having if the attempt differs. |
| `IMAGE_MAX_PER_RUN=25` (incidents) | `IMAGE_MAX_ATTEMPTS_PER_RUN=40` (attempts) | Three rungs per incident would let a 25-incident ceiling bill 75 calls. |
| `SCENE_MAX_CHARS` default 1200 | 1500, cut at the last **sentence** end | The model overshoots the stated 600–1000 target; a word-boundary cut left prompts dangling mid-clause. |

Everything else in the block shipped as written: no assumption that
`inline_data` exists, decode-before-use, aspect validation with no salvage of a
wrong-aspect return, an exact post-crop size assertion, PUT-then-HEAD
verification of content-length and content-type before the URL is returned, and
the directive-marker injection screen (an over-length scene is trimmed to a
sentence boundary first and only rejected if it is still unsafe).

**One item is only half done: the timeout.** `IMAGE_TIMEOUT_S=30` is passed to
the Gemini client's `HttpOptions` and nowhere else. The Haiku scene call goes
through `filters/model_call.create_checked` on a plain
`anthropic.Anthropic(api_key=...)`, so it carries the SDK's default timeout,
not this one. On the interactive path the effective bound is external — the War
Room's `ART_TIMEOUT_MS` (50 s) and Vercel's `maxDuration` (60 s). On the
auto-publish path (`ops/auto_publish.py` calls `generate_image` in-process) the
only backstop is Cloud Run's `--timeout=3600`.

**Key collision — which was chosen:** neither option below. The key stays
`pixel-art/{slug}.png` so a regeneration overwrites in place and never orphans
an object, and slug uniqueness is enforced upstream by `incidents.slug NOT NULL
UNIQUE` (the approve route turns the resulting `23505` into a 409 asking the
operator to edit the title). The public URL carries `?v={md5[:8]}` of the bytes,
because a stable key under a one-year `max-age` otherwise means a rectified
image never reaches anyone who already loaded the old one — measured, not
theorised.

```
EDGE CASES — implement all:

SAFETY REFUSALS (will happen regularly):
- Gemini's own safety filters will refuse some prompts, particularly on violent
  DARK EVENTS incidents. The response comes back with NO image part.
- Do NOT assume response.candidates[0].content.parts[0].inline_data exists.
  Check for an image part explicitly; its absence is a normal outcome, not an
  exception.
- Return None, log the refusal reason, and COUNT it. Emit a per-pass refusal
  rate to agent_events. If most dagger cards are being refused, that is a
  product finding, not a silent gap.

CORRUPT OR WRONG-SIZED OUTPUT:
- Validate the returned bytes decode as an image before doing anything with
  them. Truncated or non-image payload -> return None.
- Validate the returned DIMENSIONS before cropping. If the model ignored the
  aspect request and returned square, a blind centre-crop to 1200x630 destroys
  the image. Wrong aspect -> log and return None. Never salvage by squashing.
- After crop, assert the result is exactly IMAGE_WIDTH x IMAGE_HEIGHT.

R2 UPLOAD:
- Verify the upload. HEAD the object after PUT and confirm content-length
  matches the bytes sent and content-type is image/png. A zero-byte or
  truncated object with a valid URL renders as a broken image on the live site,
  which is worse than no image.
- Only return the URL after that verification passes. Otherwise return None.

KEY COLLISION:
- The R2 key is pixel-art/{slug}.png. Two incidents sharing a slug means the
  second silently overwrites the first's image, and the first card then shows
  the wrong picture. Include the incident id in the key, or assert slug
  uniqueness against the incidents table before upload. Report which you chose.

TIMEOUTS AND RETRIES:
- Hard timeout on both the Haiku call and the image call (IMAGE_TIMEOUT_S,
  default 30). This path blocks an operator's approve click.
- IMAGE_MAX_RETRIES=1. One retry on transient network failure only — never on
  a safety refusal, never on a validation failure. Exhausted -> None.

PER-PASS CEILING:
- IMAGE_MAX_PER_RUN (default 25), matching AUTO_PUBLISH_MAX_PER_RUN. An
  ingestion bug producing 500 incidents must not produce 500 image calls.
  Ceiling reached -> stop generating, emit a warning, keep publishing.

PROMPT INJECTION:
- The Haiku prompt-writer reads title and summary derived from scraped news. A
  crafted or malformed article could inject instructions into the scene text.
  Mitigations: hard-cap the scene paragraph length (SCENE_MAX_CHARS, default
  1200); the template always wraps it so style, palette and exclusions cannot
  be overridden from inside; reject a scene paragraph containing directive
  markers ("ignore previous", "system:", "instead generate").

Add tests for: no image part in response; corrupt bytes; wrong dimensions;
R2 HEAD mismatch; ceiling reached; scene paragraph over length.
```

## → B5 (write-back)

**Shipped, with the answers this block asked for.** `IMAGE_RETRY_AND_RECTIFY.md`
§4 supersedes the failure handling: publication never blocks on the image, and a
failure is flagged for operator rectification instead.

- *Idempotent approve:* both mechanisms, not one. The queue-status update is a
  compare-and-set (`.eq('status','pending')`) and a lost race deletes the
  incident just inserted; `incidents.slug` is `UNIQUE`, so a duplicate insert
  bounces as `23505` → 409.
- *Permanent failure marker:* `incidents.image_status` (migration **014**,
  CHECK-constrained by **015**), plus `image_prompt` and `image_attempts`.
  Vocabulary: `ok | suppressed | refused | transient | invalid | skipped |
  pending | no_image_final`. `suppressed` and `no_image_final` are terminal — a
  backfill must never retry them, and suppressions never enter the
  rectification queue, or the queue becomes a prompt to override guardrail #5.
- *Timeout budget:* nested deliberately — `IMAGE_TIMEOUT_S=30` (per external
  call) < `ART_TIMEOUT_MS=50 s` (War Room → agents) < `maxDuration=60` (Vercel).
  The platform kill must never be the one that fires: it lands before the
  insert and would lose the whole approval, not just the picture.

```
EDGE CASES:

IDEMPOTENT APPROVE:
- Generation now blocks the approve request for 5-10 seconds. An impatient
  operator will click twice. Without idempotency that is two incidents from one
  queue row.
- Make approve idempotent: check the queue row's status inside the transaction
  and reject a second approve, or use a unique constraint on the incident slug.
  Report which you chose and prove it with a double-click test.

ORPHANED R2 OBJECTS:
- If generation succeeds and the INSERT then fails, the image is in R2 with no
  incident referencing it. Storage leak, not a correctness bug. Log the orphan
  key at warning level so it is recoverable; do not build cleanup tooling now.

PERMANENT FAILURE MARKER:
- If generation returns None, record on the incident that image generation was
  attempted and failed (or was suppressed), distinguishing the two. Without
  this, any future backfill job will retry suppressed incidents forever — an
  infinite loop spread across passes.
- Suppressed and failed must be distinguishable. A backfill should retry
  failures and never retry suppressions.

TIMEOUT BUDGET:
- The approve endpoint needs a total timeout exceeding IMAGE_TIMEOUT_S with
  margin, or the HTTP layer will abort mid-generation and leave the operator
  with no feedback and a possible orphan.

Verify with output pasted: double-click approve produces exactly one incident;
a suppressed incident publishes with null and is marked suppressed, not failed.
```

## → B6 (house cleaning)

**Done.** `modal` and `toml` are out of `requirements.txt` (the removal is
recorded there as a comment, alongside the `train_lora.py` step the file
referenced that never existed in the repo), and no first-party module imports
either. `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` were dropped from the env
reference on 2026-08-02.

```
EDGE CASE:

- Removing modal from requirements.txt while any module still imports it turns
  a working deploy into an ImportError on the next cold start — and Cloud Run
  min-instances is 0, so the failure appears at the next scheduled trigger, not
  at deploy time.
- Before removing: grep for `import modal` and `from modal` across the whole
  repo and paste the result. After removing: create a clean venv, install from
  requirements.txt, and import every module under packages/agents. Paste that
  output. Do not rely on the existing venv, which already has modal installed.

This is the same class of failure as the httpx pin recorded in
requirements.txt — a file that had become uninstallable from scratch while the
local venv kept working.
```

---

# 4. What I am NOT asking for

Deliberately out of scope, so nobody gilds it:

- Retry queues or dead-letter handling for failed images. Log and move on; the
  frontend already degrades to the placeholder. (Still true of *automatic*
  retry. What was added afterwards, in `IMAGE_RETRY_AND_RECTIFY.md`, is an
  operator-driven rectification queue keyed on `incidents.image_status` — a
  human clicking a button, not a background queue, and suppressions are
  excluded from it by design.)
- Orphaned-R2 cleanup tooling. Log the keys; sweep manually if it ever matters.
- Backfilling images across the existing archive. Separate job, batch tier,
  after the live path is proven.
- Any attempt to "repair" a wrong-aspect or corrupt image. Return None. A
  missing image is a placeholder; a mangled image is on the front page.
