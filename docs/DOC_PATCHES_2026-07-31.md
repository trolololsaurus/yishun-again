# Doc patches — 2026-07-31

Apply before starting Claude Code. Each patch gives the anchor text to find and
what to replace it with. **Locate by anchor text, not line number** — line
numbers are from commit `1b69335` and will drift.

Ordered by priority. P1 items are actively misleading and caused wrong
assumptions during the July audit.

---

## P1 — `CLAUDE.md`

### 1.1 Guardrail #2 wording — Reddit is now a signal source too

**Find:**
```
2. Sources with `type = 'signal'` (EDMW) are never included in `source_urls`.
```

**Replace with:**
```
2. Sources with `type = 'signal'` (EDMW, Reddit) are never included in
   `source_urls`.
```

### 1.2 Enforcement status block — stale, contradicts QA_BACKLOG

The block beginning `⚠️ **Enforcement status (June-2026 QA...` states C1/C2/C4
are unenforced. `docs/QA_BACKLOG.md` records all three as landed, and migration
`010_qa_hardening.sql` exists.

**Replace the entire blockquote with:**
```
> **Enforcement status (July 2026).** C1, C2 and C4 closed — see
> `docs/QA_BACKLOG.md` and migration `010_qa_hardening.sql`.
> - **#1** DB CHECK corrected to `cardinality(source_urls) >= 1` (was
>   `array_length(...)`, which returns NULL for an empty array and therefore
>   rejected nothing).
> - **#2** enforced in `orchestrator.py` via `classifiers/source_allowlist.py`;
>   any `type='signal'` domain is stripped from `source_urls`.
> - **#4** Stage 2 now zeroes confidence on the political marker rather than
>   only logging it.
> - **#3** still has no programmatic check — operator-gate only. Under
>   unattended operation this means no gate. Highest remaining guardrail gap.
```

### 1.3 New guardrail #5 — image suppression

**Append to the numbered guardrail list:**
```
5. Image generation is suppressed when an incident carries a `suicide` or
   `self-harm` tag. `pixel_art_url` stays null; the frontend placeholder
   handles it. Deliberately narrow — severity, death count and confidence are
   not consulted, and all other categories generate normally.
```

### 1.4 Art pipeline pointer

**Find:**
```
Share cards: rendered via OG meta tags — no separate image generation. The pixel art image (already generated for incident page) doubles as the OG image.
```

**Replace with:**
```
Share cards: rendered via OG meta tags — no separate image generation. The
pixel art image doubles as the OG image, which is why generated images must be
exactly 1200×630 (the dimensions are hardcoded in
`apps/web/app/incidents/[slug]/page.tsx`).

**Art pipeline:** see `docs/ART_PIPELINE.md`. The SDXL/Modal/LoRA pipeline was
removed in July 2026 and replaced with `gemini-3.1-flash-lite-image`. TechSpec
§9 is historical — do not build from it.
```

---

## P1 — `docs/AUTONOMY.md`

### 2.1 Blocker table — add image suppression row

**Find the row:**
```
| Not political | `political_marker` | Guardrail #4. Stage 2 forces confidence 0, so this is unreachable; asserted as defence in depth |
```

**Insert immediately after:**
```
| No `suicide` / `self-harm` tag | `image_suppressed` | Guardrail #5. Blocks image generation only — the incident still publishes, with `pixel_art_url` null |
```

### 2.2 Political flag must fail loud

**Append to the section covering guardrail #4:**
```
**Failure mode.** `political: true` forces `confidence = 0.0`, which means the
incident silently never publishes and no notification is raised. Under
unattended operation the story is lost without trace. Planned fix (Track A, A9):
route to a distinct flagged state, emit an operator notification via
`ops/notify.py` subject to the dedup ledger, and write an `agent_events` row at
level `warning`. The guardrail is not weakened — it is made audible.
```

---

## P1 — `.env.example`

### 3.1 CLUSTER_BEFORE_WRITE comment — stale

**Find:**
```
on — write one row per cluster [not wired yet; treated as shadow]
```

**Replace with:**
```
on — write one row per cluster. Wired in PR #42 (July 2026). Code default
is still `off`; the deployed value lives in Cloud Run env vars.
```

### 3.2 Undocumented and new vars

**Append:**
```
# ── Clustering ─────────────────────────────────────────────────────────────
# Max LLM judge calls per pass. Becomes unused once batched grouping lands
# (Track A, A3) — a single grouping call replaces pairwise judging.
CLUSTER_MAX_JUDGES=20

# ── Stage 2 ────────────────────────────────────────────────────────────────
# Write-half model. Env-overridable so rollback is config, not redeploy.
STAGE2_WRITE_MODEL=claude-sonnet-4-6
# Summary length = min(1600, RATIO * total_source_body_chars). Derive from
# real published incidents before trusting the default.
STAGE2_SUMMARY_RATIO=0.35

# ── Art pipeline ───────────────────────────────────────────────────────────
# NEVER hardcode. Google has retired models ahead of published dates.
# Fallback if aspect-ratio control is unavailable: gemini-3.1-flash-image
IMAGE_MODEL=gemini-3.1-flash-lite-image
# Batch mode halves cost; generation is post-approval so latency is free.
IMAGE_USE_BATCH=true
IMAGE_WIDTH=1200
IMAGE_HEIGHT=630
```

---

## P2 — `docs/YishunAgain_TechSpec_v1_9.md`

Five targeted replacements. Do **not** regenerate the file.

### 4.1 Changelog — add a v1.10 row

**Append to the version table:**
```
| 1.10 | July 2026 | **Art pipeline rebuilt.** SDXL/Modal/LoRA removed entirely — the custom `yishunagain_v1` LoRA was never loaded by the deployed code, and the CivitAI SD1.5 replacement was never wired (base model stayed SDXL). Replaced with `gemini-3.1-flash-lite-image` at $0.0336/image, no GPU, no weights, no Modal. Prompt now written by Haiku from the **finished incident** after clustering and consolidation, not from raw sources and not per-candidate. Operator-editable in War Room. Output 1200×630 to match hardcoded OG dimensions; generated before insert to avoid ISR staleness. Guardrail #5 added (suicide/self-harm tag suppression). §9 superseded by `docs/ART_PIPELINE.md`. |
```

### 4.2 §9 ART PIPELINE — supersede, do not delete

**Insert immediately below the `## 9. ART PIPELINE` heading:**
```
> ⚠️ **SUPERSEDED — July 2026.** This section describes the removed SDXL/Modal
> pipeline and is retained for history only. Two claims below are false: the
> LoRA was never loaded by the deployed code, and `avr_loss=nan` is a hard
> training failure, not a logging quirk. See `docs/ART_PIPELINE.md` for the
> current design and `docs/ART_PIPELINE.md` §7 for what went wrong.
```

### 4.3 Tech stack table — image gen row

**Find:**
```
| Image gen | Modal.run | — | SDXL + LoRA yishunagain_v1 (trained, 456.5MB on R2) |
```

**Replace with:**
```
| Image gen | Gemini API | `gemini-3.1-flash-lite-image` | Nano Banana 2 Lite, $0.0336/img. No GPU, no weights. See `docs/ART_PIPELINE.md` |
```

### 4.4 Directory tree comment

**Find:**
```
│   │   ├── art/                # Pixel art prompt generation + Modal.run calls
```

**Replace with:**
```
│   │   ├── art/                # Image prompt (Haiku) + Gemini image API calls
```

### 4.5 Build tracker and risk register

**Find:**
```
Step 13: ✅ Art pipeline — LoRA trained + deployed, generation ~12s
```
**Replace with:**
```
Step 13: ⚠️ Art pipeline — SDXL/LoRA removed July 2026 (never functioned as
documented). Rebuilt on Gemini image API — see docs/ART_PIPELINE.md
```

**Find:**
```
| `avr_loss=nan` in LoRA training | Low | Monitor | Logging quirk, generation works |
```
**Replace with:**
```
| `avr_loss=nan` in LoRA training | — | Resolved | NOT a logging quirk. Hard training failure — abort the run and verify base-model compatibility. Moot: LoRA training removed. |
```

### 4.6 Pixel art prompt guide (§ around the Stage 2 schema)

The guide instructing `"16-bit JRPG pixel art style, Yishun HDB environment"` is
SDXL-era tag-soup and contradicts the current spec.

**Replace the guide block with:**
```
Pixel art prompt guide: see `docs/ART_PIPELINE.md` §3. Summary — prose not tag
soup, exclusions inline (no negative prompt parameter exists), characters
described positively as JRPG sprites with readable faces, art style stated
positively rather than as negated render settings.
```

---

## P2 — `docs/INGESTION_CHANGELOG.md`

The deferred-items section still lists three closed items as open.

**Replace the deferred-items list with:**
```
**Closed since this list was written:**
- Daily trigger — Cloud Run + Cloud Scheduler (14:58 SGT), deployed.
- Adapter coverage — 15 sources live, not 2.
- Error surfacing — scrapers raise `ScraperError` / `ScraperBlocked` instead of
  returning `[]`. Stomp had been silently dead.

**Still open:** see Track A in the current work plan — consolidation prompt
caching, batched judging, the cluster size-cap decision, and the numeric
locality veto.
```

---

## P3 — `CLAUDE_CODE_PROMPTS.md`

Prompt 1.1 is stale. It says delete `pixel_art_prompt`, which was correct when
art was staying dormant. The new workflow changes it.

**Replace Prompt 1.1 in full with:**
```
Goal: stop Sonnet generating pixel_art_prompt on every draft, and move image
prompt authoring to a separate Haiku call that runs later in the pipeline.

Rationale: the field is written on every candidate draft and read by nothing —
the generator never consumed it. Under the new design the prompt is written
once per SURVIVING incident, after clustering and consolidation have collapsed
duplicates, so nothing is generated for a candidate that gets merged or
skipped.

Part 1 — remove from the Sonnet write path:
- Delete "pixel_art_prompt" from the JSON schema block in STAGE2_SYSTEM_PROMPT.
- Remove the clause instructing the model to reflect the classification in the
  pixel art prompt.
- Remove it from the `required` tuple in the response validator.
- Do NOT delete the DB column and do NOT touch pixel_art_url in this task.

Part 2 — do NOT build the Haiku prompt-writer here. It is specified in
docs/ART_PIPELINE.md §3 and belongs to Track B. This task only removes the
Sonnet-side generation.

Part 3 — report every remaining reference to pixel_art_prompt with file and
line, including apps/war-room (QueueCard.tsx, lib/types.ts,
api/queue/[id]/approve/route.ts) and packages/agents/consolidation/queue_row.py.
Do not modify them; Track B B4 handles them.

Verify: full test suite, paste output. Baseline is 18/18.
```

---

## Sequencing

Apply P1 before any Claude Code session — those are the files Claude Code reads
first, and stale guardrail status is the most dangerous kind of drift.

P2 and P3 can follow. `ART_PIPELINE.md` is new and drops in as-is.
