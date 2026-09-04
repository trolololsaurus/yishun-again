-- Migration 023: pattern auto-append + operator reversal
-- Adds the two tracking arrays the confidence-gated auto-append needs, and the
-- two training_signals actions it and its undo write. Run in Supabase SQL Editor.

-- ── patterns: which ids were auto-added, and which the operator has excluded ──
-- incident_ids stays the full render list (hand-picked + auto-added).
-- auto_added_incident_ids is the subset the agent added, so the War Room can
--   show "Recently auto-added — review" and the operator can tell them apart.
-- excluded_incident_ids is the operator's reversal ledger: auto-append must
--   NEVER re-add an id listed here.
ALTER TABLE patterns ADD COLUMN IF NOT EXISTS auto_added_incident_ids UUID[] NOT NULL DEFAULT '{}';
ALTER TABLE patterns ADD COLUMN IF NOT EXISTS excluded_incident_ids   UUID[] NOT NULL DEFAULT '{}';

-- ── training_signals: the auto-append decision and its reversal ──────────────
-- 'pattern_auto_append'     — agent added an incident to a pattern (>= gate).
-- 'pattern_append_reverted' — operator removed one (auto- or manually-added).
ALTER TABLE training_signals DROP CONSTRAINT IF EXISTS training_signals_action_check;
ALTER TABLE training_signals ADD  CONSTRAINT training_signals_action_check
  CHECK (action IN ('approve', 'edit_approve', 'reject',
                    'pattern_confirmed', 'pattern_dismissed',
                    'unpublish',
                    'auto_approve', 'auto_publish_reverted',
                    'auto_update', 'update_reverted',
                    'pattern_auto_append', 'pattern_append_reverted'));
