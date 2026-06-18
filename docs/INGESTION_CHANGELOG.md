# Ingestion & Data-Quality Changelog

Companion to TechSpec v1.9. Records the schema additions, data corrections, and
architectural decisions made during the June 2026 consolidation + ingestion-design session,
so the next agent/operator has an honest trail rather than inferring intent from the DB.

---

## Schema additions (v1.9)

- **`pipeline_state`** (new) — per-source ingestion watermark store. TechSpec §3.7.
- **`pipeline_run_history`** (new) — append-only ingestion run log. TechSpec §3.7.
- **`training_signals`** (extend §3.4) — Learning Loop Phase 1: one row per War Room operator
  decision (approve/reject/re-source/link/correct), shaped so a future LoRA job (Phase 3) can
  consume it directly. See `docs/LEARNING_LOOP.md` §2.1.
- **`source_reputation`** (new) — Learning Loop Phase 1: per-domain trust accumulator, read back
  each run to weight candidate confidence. See `docs/LEARNING_LOOP.md` §2.2.
- **CULTURE content type** — not a schema change; a convention on existing columns:
  `classification='custom'` + `custom_label='CULTURE'`, severity 1, hype_meter 0, excluded
  from the Chaos Index, rendered with a 🌐 "YISHUN ON THE MAP" pin (violet/indigo accent).
  Frontend support added in `apps/web/lib/utils.ts` and `apps/war-room/lib/utils.ts`.

## Architecture decisions

- **Forward-looking ingestion = Option B (trigger-agnostic).** Full design in
  `docs/INGESTION_DESIGN.md` (APPROVED); spec summary in §4.9. Single `run_ingestion_pass()` seam
  decouples rot-prone trigger infra from verifiable ingestion logic.
- **Source model (Q1 = 1b): SG MSM primary, Google News corroboration.** The direct Singapore
  MSM scrapers are the primary spine behind the `Source` interface; Google News RSS cross-checks
  and catches misses. ("The main sauce is always Singapore MSM.") Rejected the earlier
  "Google-News-primary" framing.
- **Dateless candidates (Q2 = 2b): route to War Room, not dropped.** The operator's due-diligence
  (e.g. googling a weak item to find its real source) is recorded as training signal. War Room is
  the sourcing-model's training-data generator, not merely an approval gate.
- **Three-phase scope (Q3 reframed): Cold / Warm / Forward.** Cold Start (1980–2023) = the
  hand-built archive as a prototype the **Historical agent** enriches & discovers against (find
  more proof, enrich existing stories, discover items under existing umbrellas). Warm Start
  (2024–Jun 2026) = litmus-test window the **Futurist agent** scrapes and the operator validates.
  Forward (Jun 2026 →) = daily live pipeline. "Cold start" is NOT a first-run lookback window.
- **Cadence (Q4): daily, cloud docker.** Failed runs / bot traps / recommended after-actions
  reported to War Room (FallbackLadder → DegradedRunReport). Trigger: Cloud Scheduler → HTTP.
- **Learning Loop (`docs/LEARNING_LOOP.md`).** Phase 1 contextual learning IS built (read
  accumulated signal back into prompts/scoring; frozen models). Phase 2 graduated autonomy and
  Phase 3 LoRA fine-tuning are designed/roadmapped, NOT built. **The agent accumulates DATA in
  Supabase; it never modifies its own weights or code.** Human-in-the-loop is permanent;
  crime/named-individual content never auto-publishes. The Learning Loop is the road to the North
  Star (minimal-intervention autonomy = minimal review *volume*, undiminished human *authority*).
- **Two agents.** Historical agent (Cold/Warm enrichment & discovery against the archive) and
  Futurist agent (Forward daily live pipeline; the subject of the Learning Loop). Distinct;
  the Historical agent is not a mode of `run_ingestion_pass()`.
- **Historical scraping abandoned as structurally impossible.** Google News RSS has no historical
  archive (date operators break the feed); GDELT/Yahoo dead. The 2008–2025 archive was built **by
  hand** with court-verified consolidation, governed by `docs/CONSOLIDATION_RULES.md`.
- **Google News RSS smoke test (home IP) passed.** No bot traps at gentle cadence; sub-second
  responses; recent items reliably present but mixed into a relevance-ranked multi-year grab-bag —
  hence the orchestrator must re-filter by watermark, never trust feed order.

## Data corrections (audit pass, this session)

A fabricated-date / fabricated-URL detector swept all 53 cards. Findings: **zero fabricated
URLs remained**, zero lifecycle mismatches. Three wrong `incident_date` values were corrected
(all were placeholder/stamp artifacts from first-generation backfill, fixed to the real event
date per CONSOLIDATION_RULES rule 2):

- Yishun MRT foreign-worker death: `1990-01-01` → `2006-12-05`
- Murder of Liang Shan Shan (schoolgirl): `2026-06-11` → `1989-10-02`
- JI/al-Qaeda Yishun MRT plot: `2026-06-11` → `2001-12-01`

Earlier in the same session, additional first-gen corrections were applied and committed
(Wang Zhijian fabricated execution dates; Koh Ah Hwee fabricated ST/CNA URLs + wrong dates;
Ghib Ojisan duplicate with a hallucinated 2023 date + fake Reddit URL; Kurt Tay fabricated
Reddit URL; taxi-driver-murders and infant-murder wrong incident_dates).

## Known deferred items (debt, named not hidden)

- **Forward pipeline BUILT (steps 1–10 complete).** One forward pipeline: `run_ingestion_pass()`
  replaces the retired LangGraph `run_graph()` and the deleted `pipeline.py`. Herald preserved;
  Learning Loop Phase-1 live (and provably cannot override system-prompt guardrails — verified).
- **TRIGGER is the gating item for live autonomy.** `run_ingestion_pass()` does not fire on its
  own: the in-process APScheduler is dead under Cloud Run `--min-instances 0` (TechSpec §11.2).
  Cloud Scheduler → HTTP `/pipeline/run` (or `/run/ingest`) must be set up as a deployment task
  before the pipeline runs autonomously. Until then it runs only via manual `POST /pipeline/run`.
- **MSM adapter coverage — only CNA + Google News RSS exist.** "MSM primary" (Q1=1b) with one MSM
  adapter is thin. **Post-launch priority: wire Mothership + Straits Times adapters next** (behind
  the existing `Source` interface — `get_enabled_sources()` is the only edit point). The other ~10
  scrapers follow incrementally. Note: this is NOT a live coverage drop — the 14-scraper
  `scrape_all` path never fired in production under min-instances 0 anyway.
- **MSM adapters swallow errors.** `scrape_cna.scrape()` (and likely the other scrapers) catch
  feed errors and return `[]` rather than raising — so a blocked MSM source looks like a quiet
  news day. Tolerable for v1; before MSM adapters are trusted as the sole primary spine they need
  a raise-on-error path so the FallbackLadder can see the failure.
- **Kurt Tay duplicate** — `kurt-tay-intimate-video-case-2023-2026` (older, placeholder date)
  still live alongside the verified draft `yishun-kurt-tay-intimate-image-conviction-2026`.
  Operator to resolve (keep draft, delete old) in War Room.
- **2 Kurt Tay draft cards** + **6 `incident_links`** await operator confirmation in War Room.
- **Next.js 14.2.3 security patch** on War Room — outstanding (pre-launch).
- **Repo hygiene** — duplicate `YishunAgain_TechSpec_v1_8.md` at root (delete), reconcile the
  two `CONSOLIDATION_RULES.md` copies (canonical is now `docs/`), `.gitignore` the ~12 loose
  `.log/.txt/.json` backfill artifacts in `packages/agents/`.
- **CLAUDE.md** still references the non-existent `YishunAgain_TechSpec_v1.4.md` — repoint to
  `docs/YishunAgain_TechSpec_v1_9.md`.
- **North–South Line** title en-dash was normalised to a hyphen during the audit; revert to the
  en-dash if typographic correctness is preferred (cosmetic).
