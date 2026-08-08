# Art Pipeline

**Status: LIVE on the operator approve path** (verified against the working tree
2026-08-02). Rebuilt July 2026; replaces the SDXL/Modal/LoRA pipeline entirely.
This document supersedes TechSpec §9 in full — treat §9 as historical.

⚠️ **Anything that still calls art generation dormant, or `pixel_art_url`
"hardcoded null", is stale.** `apps/war-room/app/api/queue/[id]/approve/route.ts`
imports `generateIncidentArt` from `lib/artGenerate.ts`, calls it **before** the
incident INSERT, and writes `pixel_art_url`, `image_status`, `image_prompt` and
`image_attempts` onto the row (§6.2). The agent-side copy of the same call in
`ops/auto_publish.py` is written and wired but sits behind
`ART_GENERATION_ENABLED` (default `false`) — the two paths are gated
differently, and §6.2 says why.

The one Stage-2-era claim that is still true: **Stage 2 no longer emits
`pixel_art_prompt`**, and nothing in this pipeline wants it. The prompt does not
exist until approve time, and it is written from the finished incident (§2).

---

## 1. Model

| | |
|---|---|
| Model string | `gemini-3.1-flash-lite-image` ("Nano Banana 2 Lite") |
| Env var | `IMAGE_MODEL` — **never hardcode**, see §1.1 |
| Cost | $0.0336 / 1K image standard, **$0.0168 batch** (batch is unimplemented — §6) |
| Free tier | None. No Gemini image model has one. Paid tier only. |
| SDK | `google-genai` — already a dependency for Stage 1, no new packages |

Fallback if Lite cannot produce the required aspect ratio (see §5):
`gemini-3.1-flash-image` at $0.067 standard / $0.034 batch.

Not used, for the record: `gemini-2.5-flash-image` **shuts down 2 Oct 2026**.
All Imagen models are deprecated. `gemini-3-pro-image` is $0.134/image — 4×
Lite for quality we don't need at 1200×630.

### 1.1 Why the model string is an env var

Google has moved under this project twice: Groq folded (forcing the Stage 1
migration), and the image lane turned over three generations in nine months.
Google's own deprecation page states shutdown dates are *"the earliest possible
dates"*, and there are developer reports of models being pulled ahead of
published dates. A model swap must be a config change, not a redeploy.

Stage 1 runs `gemini-3.1-flash-lite` — shutdown 7 May 2027. No action needed.

---

## 2. Position in the pipeline

```
ingest → cluster → consolidate → Stage 2 write (title / summary)
                                        │
                                        ▼
                              war_room_queue row
                    nothing art-related is stored here —
                    no prompt, no image, no placeholder
                                        │
              approve (operator)  ──────┴──────  auto-publish (agent)
                                        │
                                        ▼
                          suppression gate  (§4)
                                        │
                                        ▼
                    Haiku writes the scene paragraph  (§3)
                          reads the WRITTEN INCIDENT,
                          never the raw source articles
                                        │
                                        ▼
               template wrap → Gemini → centre-crop → R2  (§3.8, §5, §6)
                                        │
                                        ▼
                 INSERT incident with pixel_art_url AND
              image_status / image_prompt / image_attempts  (§6.2)
```

**The prompt is generated at approve time, after clustering and consolidation —
never before.** Candidates that get merged into an existing cluster, or skipped
as duplicates, never reach this step, and neither does a row the operator
rejects: generation happens only for rows that are actually publishing. No
tokens are spent on images that would be thrown away, which is the single
largest saving in the redesign.

A corollary worth stating, because the War Room used to imply otherwise: **there
is nothing to edit at queue time.** `components/QueueCard.tsx` carries no art
field by design — the prompt does not exist yet. Prompt editing lives in the
post-publish rectification flow (`/rectify`, B4b), where `incidents.image_prompt`
holds the string that actually produced the picture on screen.

**The prompt-writer reads the finished incident, not the sources.** Input is
`title` + `summary` + `classification` + `severity` + `area_name`. The image
therefore reflects what the card actually says.

> **Known trade-off.** A thinly-sourced incident produces a compressed summary,
> so the prompt-writer sees less than the source article contained and will
> infer more of the scene. How much inference is permitted was settled as
> *bounded* inference — see §8.4.

---

## 3. The prompt-writer

**Model:** Haiku only. Never Sonnet. The task is constrained rewriting, not
composition, and the input is at most ~1600 characters of summary.
`IMAGE_SCENE_MODEL`, default `claude-haiku-4-5-20251001` — the same
env-var-not-hardcoded rule as §1.1.

### 3.1 Output must be Nano Banana compliant

Gemini image models take descriptive prose and have **no separate negative
prompt parameter**. Everything below is enforced by the system prompt:

- Flowing natural-language prose. **Never** comma-separated tag soup.
- Exclusions stated **inline**, in the prose. There is no negative prompt field.
- **No SDXL vocabulary**: no `masterpiece`, no `8k`, no `(weighted:1.2)`
  syntax, no `highly detailed` incantations.
- **Never negate render settings.** `no anti-aliasing`, `no blur`, `no 3d
  render` do nothing. State the art style positively instead.

### 3.2 Character rule — LOCKED

Verified by test, 31 July 2026. Getting this wrong produces featureless blobs.

> **Describe characters positively as JRPG sprites.** Expressive anime-styled
> face, large defined eyes, readable mouth and brow, hair rendered as distinct
> strands, clothing with visible fold shading. Sized large enough in frame that
> the expression reads clearly.
>
> **Never** instruct facelessness, silhouettes, "stylised figure", "no detailed
> facial features", or "small in frame". Those are SDXL-era workarounds for a
> model that rendered faces badly at sprite resolution. Nano Banana does not
> have that limitation, and the instructions actively destroy the output.

### 3.2a What the picture is about — incident vs place

**The single most consequential rule in this section**, arrived at by rendering
it wrong in both directions.

> Work out what this story is actually about, and make THAT the subject. There
> are two kinds and they need different pictures.
>
> **Centred on a thing happening** — a cockroach in a bowl, a fire in a window,
> a rescue, a brawl. Show that thing clearly and unmistakably, a touch larger
> than life so it reads at card size, but never so large it overwhelms what
> holds it. Give whoever is reacting to it a clearly legible expression.
>
> **Centred on a place or an occasion** — a mall opening, a new facility, a
> milestone, a closure. Then the PLACE is the subject: show it at its most
> characteristic and alive. There is no incident to depict and no one to react.
> Do NOT invent a shocked bystander, a mishap, or a dramatic object to fill the
> gap. A calm, busy, well-observed picture of the place is the correct answer.

⚠️ **Both halves were learned from failures.**

- A render came back with **no cockroach in the bowl** — a lovely picture of a
  coffeeshop with the story missing. That produced a *mandatory* hero-object
  rule.
- The mandatory rule then rendered *"Northpoint City is the largest mall in the
  north"* as **a woman recoiling from a prawn in a coffeeshop**. With no
  reactor and no dramatic object in the story, the model manufactured both.

Requiring a reaction and an oversized object unconditionally does not make
cards more vivid; it makes the prompt overwrite the story. The fork above is
what fixed it.

### 3.2b Register — honest, not cartoonish

> Whatever the scene, draw it as a real moment in a real place. **No slapstick:**
> no cartoon sweat-drops, no motion lines, no flailing limbs, no arms thrown in
> the air, nobody recoiling half out of their seat. Where someone IS reacting,
> keep their posture natural for what they are doing. Background figures mostly
> carry on with whatever that place involves.

Two failure modes bracket this, both rendered:

- **Too flat** — a documentary wide shot with a mildly puzzled subject. Correct
  in every technical respect and completely inert.
- **Too broad** — a giant sweat-drop, both arms in the air, a face filling a
  third of the frame. A cartoon, not a satirical archive of real events.

⚠️ **This section used to hardcode the coffeeshop.** It read *"they stay seated
at the table… leaning in over the bowl, chopsticks or spoon still in hand"* and
*"nearby diners carry on with their own meals"* — text that went into every
prompt, including the mall's. It is a large part of why a mall opening rendered
as a noodle scene. Keep the register setting-neutral.

### 3.3 Style anchor — LOCKED (amended 2026-07-31)

Stated positively, never as negation:

> 16-bit era Japanese RPG sprite art. Chunky visible pixels with hard-edged
> shading bands and a limited colour palette per element, in the manner of
> hand-drawn console sprite work — every edge stepped, never smooth. Dense,
> highly detailed background work with many small readable props.

⚠️ **"isometric view" was removed.** Combined with §3.4's original "deep
near-black background" it reliably produced a lit cutaway room floating in a
black void — a game asset, not an establishing shot. Verified against live
renders on 31 July 2026.

### 3.3a Composition — LOCKED

Camera and framing must be stated, not left to the model. Without this the
model picked its own framing per render, and the ends of that range are not
variations on a theme. This is the guardrail doing the job the LoRA was
supposed to do.

> Wide establishing shot of the location, viewed from a three-quarter angle at roughly standing eye level, as a detailed side-scrolling game background. The scene fills the entire frame edge to edge and continues past all four edges — this is a place, not an object sitting in space. The person at the centre of the incident is in the mid-foreground, doing whatever the moment calls for, turned enough towards the camera to read clearly at roughly a third of the frame height, placed naturally within the scene rather than looming over it. Include only the figures the scene actually calls for — never add a seated diner, a bystander at a table or any other filler person the incident does not describe. Fill the whole canvas with the location: no floating diorama, no cutaway room, no isometric doll's-house box, no vignette, no border, no letterboxing, and never an empty dark void framing the scene.

⚠️ **Two amendments, both from renders.** "The main figure dominates the near
foreground" pushed the subject into the camera and squeezed out the environment.
And naming the subject as someone who *"sits at a table, right of centre"* was
coffeeshop-specific: it put a woman eating noodles into a fire rescue, and a man
drinking coffee beside a body in a carpark brawl. Hence "the person at the
centre of the incident", and the explicit bar on filler people.

### 3.3b Physical coherence — LOCKED

The image model has no physics and no causality. It draws each thing you
mention as its own event, so an ambiguous description becomes an impossible
picture. Stated to the image model directly, and separately to the scene writer.

> Everything in the frame must be physically possible in a single moment. One source per effect: water leaves a hose only at the nozzle, smoke rises only from what is burning, one light casts one set of shadows. An object held by several people is still ONE object, continuous along its length. Nothing appears twice. Objects rest on surfaces, hang from fixings, or are held in a hand — nothing floats. Every person is standing on a floor, sitting on something, or holding on to something solid; nobody hovers, leaps through empty air, or stands on nothing. Limbs, hands and fingers attach normally and are counted correctly. Aim must match the target: whatever someone points, throws, sprays or directs travels TOWARDS the thing it is meant for, and that thing lies in that direction within the frame. A jet of water aimed at a fire arcs at the fire, never off into empty air, and everyone looks at what they are reacting to.

Every clause here replaces a specific rendered failure:

| Render | Defect | Clause |
|---|---|---|
| 1 | water from the nozzle **and** the middle of the hose | one source per effect; a shared object is still one object |
| 2 | jet arcing into empty sky, away from the fire | aim must match target |
| 3 | a man hovering between floors | every *person* stands on something — the float rule covered only objects |

The scene writer carries the matching discipline: name each object once, state
what every person is standing on, put the target and the actor in one clause
with a direction between them, and re-read asking whether the paragraph could
be a single photograph of one real moment.

### 3.4 Palette — LOCKED (amended 2026-07-31)

Warm, evenly lit and naturalistic. Shared by all three classifications:

> Warm and naturalistic, evenly lit by whatever light sources that place actually has. Muted earthy palette of worn, lived-in surfaces. Soft natural contrast with warm mid-tone shadows; nothing crushed to black, no harsh spotlighting, no heavy vignette.

⚠️ **Three removals, each rendered before being cut.** "Deep near-black
background" instructed the model to place the scene *on* a black backdrop, and
that is what it drew. "The deepest tones fall to near-black" still produced
high-contrast spotlit frames. And naming materials — *"terracotta floor tile,
pale green wall tile… daylight from the open frontage"* — imposed a coffeeshop
interior on a carpark. The palette is now about tonal treatment only, and the
light sources are whatever the place actually has.

The scene writer is separately anchored to late afternoon / early evening
unless the incident says otherwise, so time of day stops varying card to card.

> Accent colour derives from the incident's classification. The classification
> colours themselves — GOOD VIBES `#4ECDC4`, ABSURDITIES `#FFE66D`, DARK EVENTS
> `#FF6B6B` — are **permanently locked** and are referenced here, never altered.

### 3.5 Content exclusions — always present (relaxed 2026-07-31)

```
No logos, no brand names, no identifiable real business, no real company signage. Incidental lettering that genuinely belongs to this setting — a hand-written board, a notice, a painted marking — is welcome as ordinary set dressing, in English, naming no real establishment. Do not add signage the setting would not have.
```


⚠️ **The blanket text ban was removed on operator direction, and this is the
most counter-intuitive entry in this document.**

The original rule was `No text, no lettering, no signage, no numerals` alongside
the brand exclusions. It was enforced hard for one round — writing surfaces
positively restated as blank colour panels, the scene writer barred from naming
menu boards, chalkboards, posters, newspapers or plates, and instructed to fill
walls with writing-free content instead.

**It worked, and the result was worse.** A neighbourhood coffeeshop with nothing
written anywhere in it does not read as a coffeeshop — the frames came back
sterile and empty. The enforcement also cost the scene writer its word budget:
it spent its sentences on ladles and crockery shelves instead of on the incident
and the reaction, which is how a render ended up with no cockroach in it.

What is retained is the half carrying actual legal weight. **A recognisable
storefront in an image depicting an incident is a defamation exposure**, and
that risk is entirely unrelated to whether a chalkboard has words on it.
Garbled pseudo-lettering in the background is a cosmetic issue and is now
accepted deliberately.

The scene writer is correspondingly told the setting should feel **busy and
worked-in, never sparse**, with hand-written menus explicitly welcome and blank
walls ruled out.

### 3.6 Local vocabulary

Image models do not know "HDB void deck" — the subject is out of distribution
and prompting it directly produces generic or wrong results. Describe the
architecture in vocabulary the model *does* have, which a Singaporean reader
still recognises instantly:

| Instead of | Write |
|---|---|
| HDB void deck | colonnaded open ground-floor undercroft, thick pillars in rows |
| HDB block face | a tower face of repeating grid windows |
| bamboo laundry poles | laundry poles jutting from each window like banner staves |
| common corridor | open-air corridor with a low parapet running its full length |
| coffeeshop / kopitiam | neighbourhood coffeeshop, formica tables, stacked plastic stools |

### 3.7 Scene length and drift control

Consistency across cards is a *sampling* problem as much as a prompt problem.
Sampled three times at temperature 0.7 on one incident, the scene writer
produced 831 / 567 / 825 characters across three different times of day, and the
short one rendered visibly sparse.

| Control | Value | Why |
|---|---|---|
| `SCENE_TEMPERATURE` | `0.25` | Low, not zero. At 0.7 the same incident wandered between mid-morning and late afternoon. At 0.0 every card would read stamped from one template. |
| `SCENE_MIN_CHARS` | `560` | Under it the scene is rewritten **once** and then accepted regardless — a thin picture beats a missing one. |
| `SCENE_MAX_CHARS` | `1500` | Injection bound. Truncation cuts at the last **sentence** end; a word-boundary cut left prompts dangling mid-clause. |
| Target in prompt | 600–1000 chars, 4–6 sentences | Stated explicitly; the model overshoots slightly, which is why the cap is 1500. |

After these, the same incident sampled 1157 / 1080 / 1193 characters, all late
afternoon or evening.

### 3.8 Prompt assembly order

Order is load-bearing and asserted in `test_image_generation.py`:

```
STYLE_PREAMBLE       §3.3   constant
COMPOSITION          §3.3a  constant
PHYSICAL_COHERENCE   §3.3b  constant
<scene paragraph>           Haiku — the ONLY model-written part
PALETTE[class]       §3.4   constant
CONTENT_EXCLUSIONS   §3.5   constant
```

Style, framing and the rules of the world lead, so all three are established **before** the model reads
any scene content. Palette and exclusions land last, where they are least likely
to be forgotten. The scene sits in the middle and cannot displace any of the
five — which matters because scene text descends from scraped news and is
therefore hostile input by default (`SCENE_MAX_CHARS` plus a directive-marker
screen bound it).

Each guardrail in §3.2a–3.5 was added to fix a specific rendered failure, and
several then suppressed the next one — the prop ban crowded out the incident,
the exaggeration fix crowded out the environment. The precedence above is what
resolves that, and inverting it will reintroduce whichever failure sat below.

---

## 4. Guardrail #5 — detector + policy

This is legal guardrail #5 (CLAUDE.md), and it splits in two: a **detector**
(`art/suppression.py`) that decides whether an incident is a suicide / self-harm
story, and a **policy** (`art/generate_image.py` + `art/sensitive_scene.py`) that
decides what happens once it is. The detector runs **before** the Haiku call — no
point paying for a scene the free writer must never write for these incidents.

### 4a. The detector

Implemented in `art/suppression.py`. Guard: `test_image_suppression.py`.
Enforced in the generator, not in the UI:

| Call site | When it runs |
|---|---|
| `generate_image()` | first statement — before the slug check, before any Haiku or Gemini call |
| `render_prompt()` (operator rectify, B4b) | when the caller passes `incident`; `apps/war-room/.../rectify/route.ts` always does |

`render_prompt`'s parameter defaults to `None`, so a caller that omits `incident`
gets **no** suppression check. That is the one seam in the gate, and any new
caller of `/art/rectify` must send the incident.

```python
SUPPRESS_TAGS = frozenset({"suicide", "self-harm"})
SUPPRESS_PHRASES = ("suicide", "self-harm", "self harm",
                    "took his own life", "took her own life", "took their own life")

def suppress_image(incident: dict) -> bool:
    # tag check OR deterministic phrase match over title + summary
```

⚠️ **This is not the tag-only check this section originally specified.** `tags`
is written by the Haiku classifier, so a tag-only gate makes the one check that
must not fail depend on a model output — and the classifier does sometimes omit
a `suicide` tag on a suicide story. The gate therefore **ORs** the tag check
with a deterministic phrase match over the incident's own title and summary, so
it fires on the Blk 737 case whether or not the classifier tagged it. See
`docs/EDGE_CASES_AND_HARDENING.md` §1.2.

Two further properties:

- **Total.** Any input returns `True` or `False`, never an exception — a gate
  that raises is a gate that did not run.
- **Fails closed.** An input it cannot read returns `True` (suppress).
  Over-suppression costs a placeholder; under-suppression does not.

Deliberately narrow. Severity, death count, and classifier confidence are **not**
consulted. Fatalities, violence, fires, crime scenes and severity-5 incidents
all generate normally.

### 4b. The policy — respectful tableau (default) or suppress (rollback)

Set by `SENSITIVE_INCIDENT_ART` (default `respectful`, rollback `suppress`; any
other value resolves to `suppress` — fail toward the guardrail). Added 2026-08-09
on operator direction; before it, a detected incident was always suppressed.

**`respectful`** renders a fixed, non-graphic **police/SCDF-response scene** —
`art/sensitive_scene.py`, guard `test_sensitive_art.py`. `incident_kind()` picks
one of three so the picture matches the outcome:

- **fatal** (default) — a shut blue fast-deploy police privacy tent as the focal,
  police officers, tape, a patrol car.
- **indoor** — a death inside a unit (person conveyed to hospital): an SCDF
  ambulance response at the block, **no** ground body tent.
- **rescue** — a rescue where nobody died: an SCDF inflatable air cushion and
  responders, **no** body tent (a tent there would be plain wrong).

All three: police labelled POLICE (never "SPF"), officers in the standard cap
(never a tudung — that cue is for residents), and the block's real number painted
on the facade (never invented). What makes it safe rather than sensational:

- **Fully deterministic.** Haiku never writes it, so the scraped summary never
  becomes picture content. Only a place-TYPE (HDB block / void deck / carpark /
  corridor) is inferred, and water settings fall through to a neutral default
  rather than depict water.
- **Never the act.** The tent is shut; the scene names no body, method, fall,
  blood, weapon or distress. `scene_is_clean()` screens the assembled scene
  against a forbidden-word set before it is used.
- **Fails toward the guardrail.** An un-clean scene, an unreadable incident, or a
  safety refusal from the image model all fall back to `suppressed` (no image).
  The scene is never softened/mutated to get one past the filter, and the
  operator rectify path re-renders the tableau rather than honouring a hand-typed
  prompt. A successful render carries `image_status='ok'` like any other image.

**`suppress`** is the original behaviour: `pixel_art_url` stays `null`,
`image_status` is written as `suppressed`, and the frontend degrades to the
`PIXEL ART · COMING SOON` placeholder and `og-default.jpg`. No error, no retry.

`suppressed` is **terminal**. It is absent from `RECTIFIABLE_STATUSES`
(`apps/war-room/lib/types.ts`), so a suppressed row never appears in the
rectification queue, the rectify route rejects it explicitly, and there is
deliberately no operator override. That allowlist is a `.in()` rather than a
`.neq('image_status','ok')` for exactly this reason — an exclusion filter is one
careless edit away from inverting.

---

## 5. Output size

**1200 × 630.** Non-negotiable — `apps/web/app/incidents/[slug]/page.tsx:29-31`
declares those exact dimensions in the OpenGraph metadata. A mismatch means the
OG tags misreport the image to every crawler and share preview.

1200×630 is 1.905:1, which is not a standard generation ratio. **Request 16:9
and centre-crop** — a uniform 6.7% vertical trim that leaves the pixel grid
intact.

> ⚠️ **Do not** scale non-uniformly to hit the target. The old SDXL path did
> `1024×1024 → resize(1200, 630)`, a 1:1 → 1.9:1 squash with NEAREST resampling,
> which produced unevenly sized pixels and horizontal stretch. For pixel art
> that defeats the entire aesthetic.

~~**Open blocker:** confirm Lite exposes `image_config` aspect-ratio
control.~~ ✅ **Closed 2026-07-31** — see §8.1. `_call_image_model` sends
`types.GenerateContentConfig(response_modalities=["IMAGE"],
image_config=types.ImageConfig(aspect_ratio="16:9"))` and Lite honours it.

The blocker left a permanent tripwire behind it. `crop_to_target` measures what
came back and raises `_Invalid` when the aspect is more than `ASPECT_TOLERANCE`
(0.06) off 16:9 — tight enough that a square return can never pass — and the
observed dimensions are logged on **every** call. So a model that quietly stops
honouring the request surfaces as `image_status = 'invalid'` and a log line,
never as a squashed image on the front page. The fix in that case is one env
var: `IMAGE_MODEL=gemini-3.1-flash-image`.

---

## 6. Delivery

Upload to R2 at `pixel-art/{slug}.png`, public URL
`https://assets.yishunagain.com/pixel-art/{slug}.png?v={md5[:8]}` — the version
suffix is mandatory, see §8a.

The object is PUT, then **HEAD-verified** for content-length and content-type
before the URL is returned. A zero-byte or truncated object behind a valid URL
renders as a broken image on the live site, which is worse than no image.

Batch tier (§1) is **not** used on this path — see
`docs/EDGE_CASES_AND_HARDENING.md` §1.1. Batch turnaround is up to 24 hours and
cannot back an approve click.

`IMAGE_USE_BATCH=false` is declared in `.env.example` and read by **nothing**:
`art/generate_image.py` has no batch branch, so today every call is standard
tier by construction. That is the correct behaviour for this path; it is
recorded here so nobody assumes a switch exists that would need flipping, or
that setting it to `true` would do anything. Batch remains a bulk-archive-only
idea, unimplemented.

### 6.1 Generate before insert — not fire-and-forget

```
approve → suppression gate → Haiku prompt → render → R2 upload
        → INSERT incident WITH pixel_art_url already set
```

The old design generated asynchronously after publish, which requires an
update-after-insert. Under Next.js caching the live page then keeps serving the
placeholder until revalidation fires — the image silently never appears despite
a correct database row.

Generating first costs the operator seconds on the approve click (irrelevant for
auto-publish, which is already a background job) and eliminates the write-back,
the orphan state, and the staleness problem entirely.

On generation failure: insert `null` with the failure status and publish anyway.
The page already handles it.

**The cost of that is a timeout race, and the ordering of the two timeouts is
load-bearing.** Vercel kills a route at `maxDuration` regardless of any
`AbortController`, and that kill lands *before* the INSERT at the bottom of the
approve handler — so it costs the whole approval, not just the picture, while
the backend carries on and still uploads to R2. Hence: approve route
`maxDuration = 60`, strictly above `ART_TIMEOUT_MS` (code default 50 s, in
`lib/artGenerate.ts`), so the in-handler abort is the one that fires. That path
degrades to `status: 'transient'` and still publishes — recoverable later from
`/rectify`, where nobody is sitting in front of a spinner. The rectify route
holds the same relationship at `maxDuration = 60` over `RECTIFY_TIMEOUT_MS`
(code default 40 s), lower because it is one attempt with no ladder.

⚠️ Both budgets are env-overridable, and setting either **above** 60 s silently
restores the failure they were sized to avoid: the platform kill wins, and a
platform kill returns nothing at all — no status, no attempt recorded, and on
the approve path no incident.

### 6.2 Two writers, both wired (one behind a switch)

Fixing only the operator path would have left every auto-published incident
imageless — which under the autonomy target is most of them. Both are done:

| Writer | Calls | Gate |
|---|---|---|
| `apps/war-room/app/api/queue/[id]/approve/route.ts` | `generateIncidentArt()` (`lib/artGenerate.ts`) → HTTP `POST /art/generate` | `AGENTS_API_URL` + `OPS_TOKEN` must both be set, else `status: 'pending'` |
| `packages/agents/ops/auto_publish.py::_generate_art` | `art.generate_image.generate_image()` in-process | `ART_GENERATION_ENABLED` (default **`false`**) → `status: 'pending'` |

Each writes four columns onto the incident row:

```
pixel_art_url    the ?v=-suffixed R2 URL, or null
image_status     ok | suppressed | refused | transient | invalid | skipped
                 | pending | no_image_final
image_prompt     the full assembled prompt of the last attempt
image_attempts   [{n, prompt, outcome, reason}] — what was tried, what was refused
```

**Why the War Room goes over HTTP rather than reimplementing.** The generator is
Python and owns the guardrail-#5 gate, the softening ladder, the 16:9
centre-crop and the HEAD-verified upload. A TypeScript copy would put the one
check that must not fail into two languages, in two repos, drifting
independently. `/art/generate` and `/art/rectify` (`main.py`) are that bridge,
authenticated with `X-Ops-Token`.

**Why the gates differ.** The unattended path defaults off so an unconfigured
deploy publishes exactly as it did before rather than logging a failure per
incident; turning it on needs `CF_R2_*` + `GEMINI_API_KEY` + `IMAGE_MODEL`
first. The operator path has no such switch — an operator watching an approve
click is present to see what happened, and an unreachable backend already
degrades to `pending`.

Neither path can block a publish. Every failure returns a status; the row is
inserted regardless and the frontend degrades to the placeholder.

#### Persistence — migrations 014 and 015

Both hand-applied in the Supabase SQL Editor (there is no migration runner, QA
M15). Apply in order, after 013.

- **`014_image_status.sql`** adds `incidents.image_status` / `image_prompt` /
  `image_attempts`, backfills existing rows (`pixel_art_url IS NULL → 'pending'`,
  else `'ok'`), and creates the partial index the `/rectify` view filters on. A
  null URL previously meant four different things — never attempted,
  deliberately suppressed, refused by the safety filter, or transiently failed —
  and the rectification queue must show the refusals, hide the suppressions, and
  never re-attempt a suppressed suicide story on every backfill forever. 014
  also purges the dead SDXL-era prompts from `war_room_queue`
  (`proposed_pixel_prompt` **and** the `raw_content.pixel_art_prompt` key —
  nulling only the column leaves the panel rendering stale content), with a
  `COPY` snapshot to take first.
- **`015_image_status_check.sql`** adds the CHECK 014 deliberately deferred until
  every writer set the column. Three layers already agreed on the vocabulary and
  the only one that outlives a deploy — the database — was the only one not
  enforcing it. It matters because `suppressed` and `no_image_final` are
  terminal: a bad value that merely *looks* terminal is unreachable by every code
  path that would fix it. Applied as `NOT VALID` then `VALIDATE`, after the
  step-1 SELECT returns zero rows.

⚠️ Without 014 the `/rectify` page errors outright (it says so on screen, naming
the migration) and every insert above fails on unknown columns.

### 6.3 Frontend — already complete, nothing to build

`apps/web/app/incidents/[slug]/page.tsx` already:

- selects `pixel_art_url` (line 21)
- uses it as the OG image at 1200×630, falling back to `og-default.jpg` (29–31)
- populates the JSON-LD `image` field (119)
- renders the `<img>` on the card, with the `PIXEL ART · COMING SOON`
  placeholder as the else branch (185–197)

`apps/war-room/app/incidents/[slug]/page.tsx:82-88` renders it too (its
placeholder reads `NO PIXEL ART`). `lib/types.ts` declares it in both apps; the
War Room's also carries the `ImageStatus` union, `RECTIFIABLE_STATUSES` and the
`isImageStatus()` guard that validates a status crossing the process boundary
from the agents backend.

---

## 7. What was removed, and why

Recorded so nobody resurrects it.

### 7.1 The custom LoRA never worked

TechSpec §9.1 claimed **"TRAINED AND DEPLOYED ✅"** — LoRA `yishunagain_v1`,
112 training images, trigger word `yishunpixel`, scale 0.85 "confirmed working",
456.5 MB on R2.

The deployed code loaded **no LoRA at all**. `generate_pixel_art.py` had only a
stub comment where the loading call had been:

```python
# LoRA: replaced with CivitAI model — see PROMPT 2
```

That replacement was never implemented. What actually ran was bare SDXL 1.0
with a pixel-art prompt — which cannot render a void deck.

TechSpec also recorded `avr_loss=nan` during training as a *"kohya_ss logging
quirk, does not affect output quality"*, risk-rated Low/Monitor. That reading
was later reversed: **`avr_loss=nan` on run 1 is a hard training failure.**
Abort the run and verify base-model compatibility before anything else.

### 7.2 The CivitAI pivot was never wired

`art/test_placeholders.py` documented the intended replacement: M_Pixel LoRA at
scale **0.6**, `clip_skip=2`, DPM++ SDE Karras, 512×768 base with hires fix.

Its own header states the finding that killed it:

> M_Pixel is a **Stable Diffusion 1.5** LoRA. It MUST be loaded onto an SD 1.5
> base — loading it onto SDXL leaves the LoRA barely attached and the output
> comes out photographic instead of pixel art.

`BASE_MODEL` remained SDXL throughout. The pivot required replacing the base
model entirely, and that never happened.

**Rule retained:** verify base-model lineage before loading any third-party
LoRA. SD1.5 weights on an SDXL base produce zero style application.

### 7.3 The prompt was never the incident's

`_build_prompt(title, classification, area_name)` never referenced `title` in
its body. The effective inputs were **`classification` + `area_name` only** —
a total prompt space of (number of areas) × 4 moods. Every dagger incident in
the same street produced a byte-identical prompt.

Meanwhile Stage 2 was writing a detailed, incident-specific `pixel_art_prompt`
on every draft, the War Room exposed it as an editable field, and operator edits
were recorded into `training_signals` — and the generator discarded all of it.

This is why the new pipeline's prompt-writer is wired to the incident content.

### 7.4 Deleted

**Code:** `art/generate_pixel_art.py`, `art/test_placeholders.py` (header
salvaged into §7.2 above before deletion).

**Dependencies:** `modal==1.4.3` (nothing else imports it — `ops/backend_health.py`
deliberately hand-rolls boto3 to avoid pulling it into a health check),
`toml>=0.10.2` (kohya_ss dataset config). `Pillow` retained for image validation.

**Modal cloud and R2 — EXECUTED 2026-08-01 (B7).** What was actually there did
not match what this section predicted, so the record is the inventory, not the
plan:

| Predicted | Found | Action |
|---|---|---|
| app `yishun-pixel-art-generator` | app **`yishun-volume-inspector`** — the generator app did not exist | stopped |
| volume `yishun-hf-cache` | present (`xet`, `hub`, `accelerate`) | deleted |
| — | volume **`yishun-training-data`** (`train`, `output`, `dataset.toml`) — never mentioned here | deleted |
| secret `cloudflare-r2` | present, last used 2026-06-07 | deleted |
| R2 `lora/yishunagain_v1.safetensors`, 456.5 MB | **no `lora/` prefix at all** | nothing to delete |
| R2 test objects, 112-image training set | `placeholders/test/` ×24 (21.1 MB) + `pixel-art/test-generation-001.png`; the training set was in the Modal volume, not R2 | 25 objects deleted, 22.2 MB |

⚠️ **The 456.5 MB LoRA was never in R2.** TechSpec §9.1 called it "TRAINED AND
DEPLOYED ✅ … 456.5 MB on R2". It is not there and, per §7.1, was never loaded by
the deployed code either. The claim was wrong end to end.

Bucket after: 5 objects, 5.0 MB — one live incident image and four renders for
incidents not yet published. Deletion was guarded by a live-reference check
against `incidents.pixel_art_url`, so no object an incident points at could be
removed regardless of prefix.

Modal has no "delete app", only `stop`; the stopped app ages out on its own. The
Modal **token** is revoked from the Modal dashboard, not the CLI — still to do.

**Env vars — removed 2026-08-02.** `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` are
gone from `.env.example`; nothing under `packages/` or `apps/` reads them, and
`modal` is not in `requirements.txt`. Deleting the stub is housekeeping, not
revocation — see the line above.

> ⚠️ **Do not grep-and-delete on "LoRA".** `docs/LEARNING_LOOP.md` Phase 3
> concerns *text-model* fine-tuning on `training_signals` — an entirely separate
> roadmap item with no relationship to image generation.

---

## 8. Open decisions

1. ~~**Aspect-ratio control on Lite** (§5)~~ ✅ **CLOSED 2026-07-31.**
   `gemini-3.1-flash-lite-image` accepts `image_config.aspect_ratio="16:9"` and
   returned 1376×768. Verified by live call. Stay on Lite at $0.0336/image.
2. ~~**When the Haiku call fires**~~ ✅ **CLOSED — neither option shipped.** The
   choice was framed as lazily on queue-card open versus eagerly for every
   surviving row. What shipped is later than both: the call fires at
   approve/auto-publish time, inside `generate_image`, so it is spent on exactly
   the rows that become incidents — nothing on rows nobody reviews, and nothing
   on rows the operator rejects. The cost lands on the approve click, bounded by
   the timeout ordering in §6.1.
3. ~~**Prompt scope**~~ ✅ **CLOSED.** Haiku writes the scene paragraph ONLY.
   The template supplies §3.3 style, §3.3a composition, §3.3b physical
   coherence, §3.4 palette and §3.5 exclusions — five constants around one
   model-written middle. Implemented in `art/prompt_template.py::assemble_prompt`,
   order asserted in `test_image_generation.py`.
4. ~~**Inference beyond the summary** (§2)~~ ✅ **CLOSED — bounded inference.**
   The scene writer may add generic setting and atmosphere consistent with the
   incident (time of day, weather, architecture, ambient props, passers-by). It
   may not invent incident facts: no injuries, causes, outcomes, vehicles, named
   people, or specific identifiers the incident does not state.

## 8a. Cache busting — required

`pixel_art_url` carries a `?v={md5[:8]}` content hash. The R2 key stays stable
(`pixel-art/{slug}.png`) so regeneration overwrites in place and never orphans
objects, but the object is served with `max-age=31536000`. Measured on 31 July
2026: regenerating under a substantially changed prompt still served the
*previous* bytes, because neither the key nor the URL had changed. That silently
defeats operator rectification (B4b), whose entire purpose is replacing an image
someone has already seen. Hashing the bytes into the query string keeps the long
TTL, keeps one object per incident, and changes the URL exactly when the picture
does.

⚠️ **`generate_image` uploads BEFORE the caller decides whether to keep the
result.** There is no render-without-upload mode, so a "dry run" that only
withholds the database write still overwrites the R2 object. Verified 2026-08-01:
after several such runs the incident's stored `?v=` hash no longer matched the
bytes then in R2. Nothing broke — each `?v=` URL is cached independently at the
edge and still serves its own correct bytes — but the immutability guarantee is
only as good as the edge cache. If an old `?v=` is ever evicted it falls through
to R2 and serves the *newest* bytes under the *old* hash. Either add a
no-upload preview mode, or always persist the URL a run produced.
