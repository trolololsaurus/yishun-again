# Ingestion & Data-Quality Changelog

Companion to TechSpec v1.9. Records the schema additions, data corrections, and
architectural decisions made during the June 2026 consolidation + ingestion-design session,
so the next agent/operator has an honest trail rather than inferring intent from the DB.

---

## Schema additions (v1.9)

- **`pipeline_state`** (new) — per-source ingestion watermark store. TechSpec §3.7.
- **`pipeline_run_history`** (new) — append-only ingestion run log. TechSpec §3.7.
- **CULTURE content type** — not a schema change; a convention on existing columns:
  `classification='custom'` + `custom_label='CULTURE'`, severity 1, hype_meter 0, excluded
  from the Chaos Index, rendered with a 🌐 "YISHUN ON THE MAP" pin (violet/indigo accent).
  Frontend support added in `apps/web/lib/utils.ts` and `apps/war-room/lib/utils.ts`.

## Architecture decisions

- **Forward-looking ingestion = Option B (trigger-agnostic).** Full design in
  `docs/INGESTION_DESIGN.md`; spec summary in §4.9. Chosen over a minimal patch because it
  decouples the (rot-prone) trigger infra from the (verifiable) ingestion logic via a single
  `run_ingestion_pass()` seam — the drift-resistant choice.
- **Trigger model corrected.** In-process APScheduler is dead under `--min-instances 0`
  (§11.2). Recommended fix: Cloud Scheduler → HTTP `/run/ingest`. Flagged as a separate
  deployment task; blocking for live autonomy, not for the ingestion code or a manual launch.
- **Historical scraping abandoned as structurally impossible.** Google News RSS has no
  historical archive (date operators break the feed); GDELT/Yahoo dead. The 2008–2025 archive
  was therefore built **by hand** with court-verified consolidation, governed by
  `docs/CONSOLIDATION_RULES.md`. The autonomous pipeline's job is **forward-looking only**.
- **Google News RSS smoke test (home IP) passed.** No bot traps at gentle cadence; sub-second
  responses; recent items reliably present but mixed into a relevance-ranked multi-year
  grab-bag — hence the orchestrator must re-filter by watermark, never trust feed order.

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
