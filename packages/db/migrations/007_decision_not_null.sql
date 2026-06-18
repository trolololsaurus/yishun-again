-- Migration 007: Enforce NOT NULL on training_signals.decision
--
-- Run ONLY after all War Room writers (Phase 2 of 006) are deployed and
-- confirmed live. Every INSERT must supply `decision` before this runs.
--
-- Pre-flight check: confirm no NULL rows remain (should be 0 from 006 backfill):
--   SELECT count(*) FROM training_signals WHERE decision IS NULL;
--
-- If 0, safe to proceed:

ALTER TABLE training_signals ALTER COLUMN decision SET NOT NULL;
