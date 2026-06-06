-- Migration 003: Developing story lifecycle + pattern detection
-- Run in Supabase SQL Editor.

-- ============================================================
-- incidents — latest_source_role column + expanded constraint
-- ============================================================

-- Add column if it didn't exist from a prior migration
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS latest_source_role TEXT;

-- Drop old constraint (if any) and replace with version that includes 'timeout'
ALTER TABLE incidents DROP CONSTRAINT IF EXISTS incidents_latest_source_role_check;
ALTER TABLE incidents ADD CONSTRAINT incidents_latest_source_role_check CHECK (latest_source_role IN ('initial', 'update', 'verdict', 'correction', 'follow_up', 'timeout'));

-- Lifecycle conclusion tracking
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS concluded_at TIMESTAMPTZ;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS conclusion_type TEXT CHECK (conclusion_type IN ('verdict', 'timeout', 'operator'));

-- ============================================================
-- pattern_alerts
-- ============================================================

CREATE TABLE IF NOT EXISTS pattern_alerts (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  pattern_type    TEXT NOT NULL CHECK (pattern_type IN ('entity', 'crime_type', 'location')),
  pattern_value   TEXT NOT NULL,
  incident_ids    UUID[] NOT NULL,
  window_days     INTEGER NOT NULL,
  status          TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'confirmed', 'dismissed')),
  operator_action TEXT,
  resolved_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_pattern_alerts_status ON pattern_alerts(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pattern_alerts_type ON pattern_alerts(pattern_type, pattern_value);

ALTER TABLE pattern_alerts ENABLE ROW LEVEL SECURITY;
CREATE POLICY "operator_full_access" ON pattern_alerts USING (true) WITH CHECK (true);

-- ============================================================
-- people_profiles
-- ============================================================

CREATE TABLE IF NOT EXISTS people_profiles (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at        TIMESTAMPTZ DEFAULT NOW(),
  slug              TEXT UNIQUE NOT NULL,
  name              TEXT NOT NULL,
  aliases           TEXT[],
  incident_ids      UUID[],
  is_published      BOOLEAN DEFAULT FALSE,
  notes             TEXT,
  legal_sensitivity TEXT CHECK (legal_sensitivity IN ('low', 'medium', 'high'))
);

ALTER TABLE people_profiles ENABLE ROW LEVEL SECURITY;
CREATE POLICY "operator_only" ON people_profiles USING (true) WITH CHECK (true);
