-- Migration 009: allow 'unpublish' as a training_signals.action
--
-- The War Room unpublish route (apps/war-room/app/api/incidents/[id]/unpublish)
-- records an operator taking down an already-PUBLISHED incident. That is a
-- distinct signal from 'reject' (rejecting a queue item that was never
-- published), so it deserves its own action value. The action CHECK constraint
-- (last set in 006_phase1_apply_now.sql) never included it, so those inserts
-- were silently rejected by Postgres and swallowed by the supabase-js client —
-- the unpublish succeeded but its training signal was lost.
--
-- Nothing branches on training_signals.action (learning.py / autonomy_tracker.py
-- key on decision / reject_reason / operator_changes; recalibration.py selects
-- action but never reads it), so widening the enum is zero-risk to consumers.
--
-- Safe to run any time — DROP + re-ADD is atomic and the table stays online.

ALTER TABLE training_signals DROP CONSTRAINT IF EXISTS training_signals_action_check;
ALTER TABLE training_signals ADD CONSTRAINT training_signals_action_check
  CHECK (action IN (
    'approve', 'edit_approve', 'reject',
    'pattern_confirmed', 'pattern_dismissed',
    'unpublish'
  ));
