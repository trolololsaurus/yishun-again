-- ============================================================================
-- 018 — Undo an applied update (merge)
--
-- confirm-update (and, in PR #2, the autonomous auto-merge) mutates an
-- ALREADY-PUBLISHED incident: it appends a source URL + timeline entry, bumps
-- update_count and recomputes the dates. Until now that was one-way — once a row
-- reached status='update_approved' there was no route back, so a wrongly-merged
-- source stayed on the live incident, invisible in its source list. This adds the
-- vocabulary the undo needs; the reversal data itself rides in the existing
-- war_room_queue.raw_content jsonb (raw_content._undo_snapshot), so no columns.
--
--   1. war_room_queue.status      += 'update_reverted'
--   2. training_signals.action    += 'auto_update' (the autonomous merge in PR #2)
--                                     and 'update_reverted' (the undo, either path)
--
-- Apply by hand in the Supabase SQL Editor, after 017. Idempotent: safe to
-- re-run. There is still no migration runner (QA M15).
--
-- ⚠️ Same failure mode as 009/011: without the action values below, every
-- revert (and every auto-merge) training-signal insert is silently rejected and
-- the learning loop never sees the correction. The routes degrade gracefully
-- (the mutation still happens, the signal insert just errors), so a missing
-- migration looks like silence, not a crash.
-- ============================================================================


-- ── 1. war_room_queue: a reverted merge is its own terminal status ──────────
-- Distinct from 'update_rejected' (rejected BEFORE it was applied) — this one
-- was applied to a live incident and then undone. Keeping them separate keeps
-- the training data honest: a revert means the merge looked right enough to pass
-- the gate and was only caught after the fact.

ALTER TABLE war_room_queue DROP CONSTRAINT IF EXISTS war_room_queue_status_check;
ALTER TABLE war_room_queue ADD  CONSTRAINT war_room_queue_status_check
  CHECK (status IN ('pending', 'approved', 'rejected', 'escalated',
                    'update', 'update_approved', 'update_rejected',
                    'update_reverted'));


-- ── 2. training_signals: record the auto-merge and its reversal ─────────────
-- 'auto_update'     — an autonomous merge (PR #2), the sibling of 'auto_approve'.
-- 'update_reverted' — an applied merge undone, the sibling of
--                     'auto_publish_reverted'. decision stays within the existing
--                     CHECK ('reject' — the merge was rejected after the fact), so
--                     no decision-CHECK change is needed.

ALTER TABLE training_signals DROP CONSTRAINT IF EXISTS training_signals_action_check;
ALTER TABLE training_signals ADD  CONSTRAINT training_signals_action_check
  CHECK (action IN ('approve', 'edit_approve', 'reject',
                    'pattern_confirmed', 'pattern_dismissed',
                    'unpublish',
                    'auto_approve', 'auto_publish_reverted',
                    'auto_update', 'update_reverted'));
