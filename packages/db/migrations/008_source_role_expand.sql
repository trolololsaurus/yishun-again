-- Migration 008: Expand latest_source_role and source_timeline role vocabulary
-- Adds sentencing, appeal, appeal_dismissed to support multi-stage legal stories.
-- Safe to run any time — DROP + re-ADD is atomic and table stays online.

ALTER TABLE incidents DROP CONSTRAINT IF EXISTS incidents_latest_source_role_check;
ALTER TABLE incidents ADD CONSTRAINT incidents_latest_source_role_check
  CHECK (latest_source_role IN (
    'initial', 'update', 'verdict', 'sentencing',
    'appeal', 'appeal_dismissed', 'correction', 'follow_up', 'timeout'
  ));
