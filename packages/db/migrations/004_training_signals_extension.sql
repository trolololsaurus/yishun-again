-- Migration 004: training_signals extension for role logging + pattern outcomes
-- Run in Supabase SQL Editor.

-- New columns for role-assignment training
ALTER TABLE training_signals ADD COLUMN IF NOT EXISTS agent_role_proposed TEXT;
ALTER TABLE training_signals ADD COLUMN IF NOT EXISTS operator_role_confirmed TEXT;

-- Expand action constraint to include pattern alert outcomes
ALTER TABLE training_signals DROP CONSTRAINT IF EXISTS training_signals_action_check;
ALTER TABLE training_signals ADD CONSTRAINT training_signals_action_check CHECK (action IN ('approve', 'edit_approve', 'reject', 'pattern_confirmed', 'pattern_dismissed'));
