-- Migration 006 — Phase 1 (expand/contract pattern)
-- Apply NOW in Supabase Dashboard SQL Editor.
-- Phase 2 (writers supply `decision`) follows code deploy.
-- Phase 3 (NOT NULL enforcement) is migration 007 — separate, after deploy.
--
-- Safe to run multiple times — CREATE TABLE IF NOT EXISTS + ADD COLUMN IF NOT EXISTS.
--
-- WHAT THIS DOES:
--   1. Creates pipeline_state, pipeline_run_history, source_reputation (unblocks run_ingestion_pass)
--   2. Renames training_signals.timestamp -> created_at (confirmed: zero live readers use old name)
--   3. Adds agent_role_proposed, operator_role_confirmed + extends action CHECK (from never-applied 004)
--   4. Adds 12 new nullable ingestion-era columns (4c)
--   5. Adds `decision` column as NULLABLE + backfills from action — NOT NULL deferred to migration 007

-- ============================================================
-- 1. pipeline_state
-- ============================================================
CREATE TABLE IF NOT EXISTS pipeline_state (
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

-- ============================================================
-- 2. pipeline_run_history
-- ============================================================
CREATE TABLE IF NOT EXISTS pipeline_run_history (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  ran_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  dry_run       BOOLEAN NOT NULL DEFAULT FALSE,
  degraded      BOOLEAN NOT NULL DEFAULT FALSE,
  total_queued  INTEGER NOT NULL DEFAULT 0,
  report        JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pipeline_run_history_ran_at
  ON pipeline_run_history (ran_at DESC);

ALTER TABLE pipeline_run_history ENABLE ROW LEVEL SECURITY;

-- ============================================================
-- 3. source_reputation
-- ============================================================
CREATE TABLE IF NOT EXISTS source_reputation (
  source_domain      TEXT PRIMARY KEY,
  approvals          INTEGER NOT NULL DEFAULT 0,
  rejections         INTEGER NOT NULL DEFAULT 0,
  re_source_wins     INTEGER NOT NULL DEFAULT 0,
  trust_score        DECIMAL(4,3) NOT NULL DEFAULT 0.500,
  last_updated       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE source_reputation ENABLE ROW LEVEL SECURITY;

-- ============================================================
-- 4a. Rename timestamp -> created_at
-- (safe: grep confirmed zero live readers use the old name)
-- ============================================================
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name   = 'training_signals'
      AND column_name  = 'timestamp'
  ) THEN
    ALTER TABLE training_signals RENAME COLUMN "timestamp" TO created_at;
  END IF;
END $$;

-- ============================================================
-- 4b. Columns from never-applied migration 004
-- ============================================================
ALTER TABLE training_signals ADD COLUMN IF NOT EXISTS agent_role_proposed     TEXT;
ALTER TABLE training_signals ADD COLUMN IF NOT EXISTS operator_role_confirmed TEXT;

-- Extend action CHECK to cover pattern_confirmed / pattern_dismissed
-- (some War Room routes already write these values)
ALTER TABLE training_signals DROP CONSTRAINT IF EXISTS training_signals_action_check;
ALTER TABLE training_signals ADD CONSTRAINT training_signals_action_check
  CHECK (action IN ('approve', 'edit_approve', 'reject', 'pattern_confirmed', 'pattern_dismissed'));

-- ============================================================
-- 4c. New ingestion-era columns (all nullable)
-- ============================================================
ALTER TABLE training_signals ADD COLUMN IF NOT EXISTS queue_id                 UUID
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

-- ============================================================
-- 4d. `decision` — NULLABLE for now (NOT NULL added in migration 007
--     after all War Room writers are deployed with the decision field)
-- ============================================================
ALTER TABLE training_signals ADD COLUMN IF NOT EXISTS decision TEXT
  CHECK (decision IN ('approve','reject','approve_with_edits',
                      're_source','link_umbrella','escalate'));

-- Backfill existing 30 rows from action
UPDATE training_signals
SET decision = CASE action
  WHEN 'approve'           THEN 'approve'
  WHEN 'edit_approve'      THEN 'approve_with_edits'
  WHEN 'reject'            THEN 'reject'
  WHEN 'pattern_confirmed' THEN 'approve'
  WHEN 'pattern_dismissed' THEN 'reject'
END
WHERE decision IS NULL;

-- ============================================================
-- Verify (run these SELECTs after the above to confirm)
-- ============================================================
-- SELECT table_name FROM information_schema.tables
--   WHERE table_schema = 'public'
--     AND table_name IN ('pipeline_state','pipeline_run_history','source_reputation');
--
-- SELECT column_name, data_type, is_nullable
--   FROM information_schema.columns
--  WHERE table_schema = 'public' AND table_name = 'training_signals'
--    AND column_name IN ('created_at','decision','queue_id','agent_role_proposed')
--  ORDER BY column_name;
--
-- SELECT action, decision, count(*) FROM training_signals GROUP BY 1,2 ORDER BY 1;
