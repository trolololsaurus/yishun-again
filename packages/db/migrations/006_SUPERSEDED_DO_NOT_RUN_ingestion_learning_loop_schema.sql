-- !! SUPERSEDED — DO NOT RUN THIS FILE !!
-- This is the original draft of Migration 006, written before the
-- expand/contract split was adopted. It was NEVER applied to the live DB.
--
-- What actually ran (in order):
--   1. 006_phase1_apply_now.sql  — expand phase (nullable column, new tables)
--   2. War Room code deploy       — all writers supply `decision`
--   3. 007_decision_not_null.sql  — contract phase (NOT NULL enforcement)
--
-- Kept for reference only. Run 006_phase1_apply_now.sql + 007 instead.
-- ============================================================

-- Migration 006: Ingestion + Learning Loop schema (ORIGINAL DRAFT — see above)
-- Adds the watermark/reporting tables for the new ingestion pipeline
-- (TechSpec v1.9 §3.7 / INGESTION_DESIGN.md §8) and the Phase-1
-- contextual-learning tables (LEARNING_LOOP.md §2.1, §2.2).

-- ============================================================
-- 1. pipeline_state  (TechSpec v1.9 §3.7 / INGESTION_DESIGN.md §8)
-- One row per source. Written at the end of every run_ingestion_pass()
-- pass for that source (watermark advance, last status, failure streak).
-- ============================================================

CREATE TABLE pipeline_state (
  source_name          TEXT PRIMARY KEY,
  last_run_at          TIMESTAMPTZ,
  watermark            DATE,
  last_status          TEXT NOT NULL DEFAULT 'never_run'
                         CHECK (last_status IN ('never_run','ok','degraded','blocked','unavailable')),
  last_reason          TEXT,
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE pipeline_state ENABLE ROW LEVEL SECURITY;
-- no anon access — service_role only (agents backend)


-- ============================================================
-- 2. pipeline_run_history  (TechSpec v1.9 §3.7 / INGESTION_DESIGN.md §8)
-- One row per run_ingestion_pass() invocation, full IngestionReport as JSONB.
-- ============================================================

CREATE TABLE pipeline_run_history (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  ran_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  dry_run       BOOLEAN NOT NULL DEFAULT FALSE,
  degraded      BOOLEAN NOT NULL DEFAULT FALSE,
  total_queued  INTEGER NOT NULL DEFAULT 0,
  report        JSONB NOT NULL
);

CREATE INDEX idx_pipeline_run_history_ran_at ON pipeline_run_history (ran_at DESC);

ALTER TABLE pipeline_run_history ENABLE ROW LEVEL SECURITY;
-- no anon access — service_role only (agents backend)


-- ============================================================
-- 3. source_reputation  (LEARNING_LOOP.md §2.2)
-- One row per domain. Read back at the start of every
-- run_ingestion_pass() to weight candidate confidence.
-- ============================================================

CREATE TABLE source_reputation (
  source_domain      TEXT PRIMARY KEY,
  approvals          INTEGER NOT NULL DEFAULT 0,
  rejections         INTEGER NOT NULL DEFAULT 0,
  re_source_wins     INTEGER NOT NULL DEFAULT 0,
  trust_score        DECIMAL(4,3) NOT NULL DEFAULT 0.500,
  last_updated       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE source_reputation ENABLE ROW LEVEL SECURITY;
-- no anon access — service_role only (agents backend)


-- ============================================================
-- 4. training_signals — align with LEARNING_LOOP.md §2.1
--
-- CURRENT LIVE STATE (confirmed via direct query, 30 rows):
--   columns: id, incident_id, timestamp, action, reject_reason,
--            original_draft, edited_draft, original_classification,
--            edited_classification, original_severity, edited_severity,
--            operator_changes, agent_confidence_was
--   action values present:  approve (13), reject (17)
--   reject_reason values present: too_thin (9), noise (6), unverified (2),
--            NULL (13)  -- all within the existing CHECK, no taxonomy change needed
--   migration 004 (agent_role_proposed, operator_role_confirmed, expanded
--   action CHECK) was written but NEVER applied to the live DB. It is
--   folded into this migration (step 4b) because recalibration.py already
--   depends on those two columns and is currently broken without them.
-- ============================================================

-- 4a. LEARNING_LOOP §2.1 names the timestamp column `created_at`.
--     Renaming preserves the existing 30 rows (no data loss); only the
--     column name changes. Update any code/dashboards that still read
--     `timestamp` on training_signals after this migration runs.
ALTER TABLE training_signals RENAME COLUMN "timestamp" TO created_at;

-- 4b. Apply migration 004 (never applied to live DB)
ALTER TABLE training_signals ADD COLUMN IF NOT EXISTS agent_role_proposed TEXT;
ALTER TABLE training_signals ADD COLUMN IF NOT EXISTS operator_role_confirmed TEXT;

ALTER TABLE training_signals DROP CONSTRAINT IF EXISTS training_signals_action_check;
ALTER TABLE training_signals ADD CONSTRAINT training_signals_action_check
  CHECK (action IN ('approve', 'edit_approve', 'reject', 'pattern_confirmed', 'pattern_dismissed'));

-- 4c. New columns for the ingestion-era signal shape (LEARNING_LOOP §2.1)
ALTER TABLE training_signals ADD COLUMN IF NOT EXISTS queue_id                  UUID
                                REFERENCES war_room_queue (id);
ALTER TABLE training_signals ADD COLUMN IF NOT EXISTS source_url               TEXT;
ALTER TABLE training_signals ADD COLUMN IF NOT EXISTS source_name              TEXT;
ALTER TABLE training_signals ADD COLUMN IF NOT EXISTS source_type              TEXT;
ALTER TABLE training_signals ADD COLUMN IF NOT EXISTS proposed_classification  TEXT;
ALTER TABLE training_signals ADD COLUMN IF NOT EXISTS proposed_severity        INTEGER;
ALTER TABLE training_signals ADD COLUMN IF NOT EXISTS agent_confidence         DECIMAL(3,2);
ALTER TABLE training_signals ADD COLUMN IF NOT EXISTS corrected_classification TEXT;
ALTER TABLE training_signals ADD COLUMN IF NOT EXISTS corrected_severity       INTEGER;
ALTER TABLE training_signals ADD COLUMN IF NOT EXISTS operator_added_source    TEXT;
ALTER TABLE training_signals ADD COLUMN IF NOT EXISTS linked_incident_id       UUID
                                REFERENCES incidents (id);
ALTER TABLE training_signals ADD COLUMN IF NOT EXISTS operator_note            TEXT;

-- 4d. `decision` (LEARNING_LOOP §2.1: NOT NULL CHECK (...)).
--     30 existing rows have no `decision` value, so: add nullable ->
--     backfill from the existing `action` column -> enforce NOT NULL.
ALTER TABLE training_signals ADD COLUMN IF NOT EXISTS decision TEXT
  CHECK (decision IN ('approve','reject','approve_with_edits',
                       're_source','link_umbrella','escalate'));

UPDATE training_signals SET decision = CASE action
  WHEN 'approve'           THEN 'approve'
  WHEN 'edit_approve'       THEN 'approve_with_edits'
  WHEN 'reject'             THEN 'reject'
  WHEN 'pattern_confirmed'  THEN 'approve'
  WHEN 'pattern_dismissed'  THEN 'reject'
END
WHERE decision IS NULL;

ALTER TABLE training_signals ALTER COLUMN decision SET NOT NULL;

-- 4e. reject_reason taxonomy: TechSpec §1.6 dismiss-reason taxonomy
--     (noise / duplicate / unverified / too thin / legal risk) is the
--     SAME taxonomy already enforced by the existing CHECK on this
--     column (migration 001). No change required.
