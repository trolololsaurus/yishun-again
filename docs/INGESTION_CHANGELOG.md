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

> **2026-07-30 — see `docs/PIPELINE_CHANGES_2026-07-30.md`** for the cost +
> classification programme: batched grouping (replacing pairwise judging +
> union-find), batched consolidation, the locality veto, the Haiku write model
> with a source-proportional length cap, the groundedness and casualty
> cross-checks, and the `max_tokens` truncation guard. Several items in the list
> below were closed by earlier work and are struck through with the evidence.

## Known deferred items (debt, named not hidden)

- **Forward pipeline BUILT (steps 1–10 complete).** One forward pipeline: `run_ingestion_pass()`
  replaces the retired LangGraph `run_graph()` and the deleted `pipeline.py`. Herald preserved;
  Learning Loop Phase-1 live (and provably cannot override system-prompt guardrails — verified).
- ~~**TRIGGER is the gating item for live autonomy.**~~ ✅ **CLOSED (verified
  2026-07-30).** Cloud Scheduler fires `POST /orchestrator/daily` at 14:58 SGT.
  `baseline_report.py` shows 88 `agent_runs` across 7 agents in a 14-day window,
  including `daily_orchestrator`. APScheduler remains dead under
  `--min-instances 0`, as designed — that is the reason Cloud Scheduler exists,
  not an outstanding gap.
- ~~**MSM adapter coverage — only CNA + Google News RSS exist.**~~ ✅ **CLOSED
  (verified 2026-07-30).** `get_enabled_sources()` returns **15** live sources —
  confirmed by a live pass this session. RSS-dated MSM: CNA, Mothership, Straits
  Times, MustShareNews, The Independent, Yahoo. HTML-scraped MSM: AsiaOne, Stomp,
  Zaobao, Shin Min, Berita Harian, Tamil Murasu. Plus Google News RSS
  (corroboration) and Reddit + EDMW (signal).
- ~~**MSM adapters swallow errors.**~~ ✅ **CLOSED.** Scrapers now **raise**
  `ScraperError` / `ScraperBlocked` on a source-level failure; the adapters
  translate those to `SourceBlockedError` / `SourceUnavailableError`. An empty
  result therefore means "no Yishun news", not "something broke quietly" — Stomp
  sat silently dead for weeks under the old behaviour.
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

---

## June-2026 feed + data-integrity + QA session

A working session covering the public feed, the consolidation pipeline, the War Room,
and a full-codebase QA sweep. Honest trail of what changed and what's still open.

### Schema / migrations
- **008** — `incidents.latest_source_role` CHECK expanded to include `sentencing`,
  `appeal`, `appeal_dismissed` (multi-stage legal stories).
- **009** — `training_signals.action` CHECK expanded to include `unpublish` (the War
  Room unpublish route writes it; before 009 those inserts were silently rejected by
  Postgres and swallowed by supabase-js, so unpublish signals were lost).

### Pipeline
- **Consolidation now dedups against the pending `war_room_queue`, not just published
  incidents** (`consolidation/check.py`). Same-event reports arriving across passes
  before approval collapse to one row via `action='skip'` (orchestrator already drops
  skips). New `QUEUE_FETCH_LIMIT` in `consolidation/rules.py`.
- **Google News URL resolver fixed** (`scrapers/_gnews_helpers.py`). Modern
  `/rss/articles/CBMi…` links are resolved via Google's `batchexecute` RPC; fully
  exception-guarded (degrades to the raw URL — never breaks a pass). Restores the
  `Candidate.url` canonical-URL contract and the cheap URL-exact dedup gate.
- **War Room `confirm-update` date corruption fixed** — it stamped `new Date()` as both
  the merged timeline date and `incident_date`, floating merged stories to the top of
  the feed dated "today". Now uses the candidate's real article date, falling back to
  the incident's existing date (never the future). (PR pending merge at session end.)

### Frontend (feed / incident display)
- **DEVELOPING badge + banner removed.** `is_developing` still drives feed sort + the
  report-count line.
- **Lightning (⚡) = corroboration**, `max(0, corroboration_count − 1)`, derived live in
  card / map popup / detail. Legacy `hype_meter` no longer read.
- **Story timeline collapses same-date nodes**; renders only with 2+ distinct dates.
- **"Time to verdict"** computed from the last verdict/sentencing/appeal entry in
  `source_timeline` (helpers `lastVerdictEntry` / `verdictNoun`), never `incident_date`.
- **War Room draft 404 fixed** — operator-only preview route
  (`apps/war-room/app/incidents/[slug]`) renders drafts via the secret key; the list
  routes Live → public View, Draft → internal Preview.

### Data corrections (live DB, via one-off scripts)
- Resolved every `news.google.com` source URL → real publisher + stamped the real
  article date across published incidents.
- Reconsolidated duplicate 2026 incidents into canonicals (re-queued to War Room as
  `update` candidates); feed 45 → 26 published 2026 rows, Chaos 94 → 51.
- Sourced 10 unsourced heritage cards (operator-supplied references).
- **Pending (`cleanup_corrupted.py --apply`):** recompute `corroboration_count =
  len(source_urls)` (13 stale rows) and repair 8 incidents whose `incident_date` was
  forced to `2026-06-23` by the merge bug.

### QA
- Full-codebase functional QA produced **`docs/QA_BACKLOG.md`** — 39 ranked issues
  (4 Critical, 8 High, 16 Medium, 11 Low) with a fix for each. Headline: 3 of the 4
  "hardcoded — never remove" legal guardrails are not actually enforced in code
  (QA C1/C2/C4), and UTM analytics inserts are blocked by RLS (QA C3).
