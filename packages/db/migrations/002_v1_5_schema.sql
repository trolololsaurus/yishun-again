-- Migration 002: v1.5 schema changes
-- Incident consolidation, incident_links table, updated war_room_queue status values.
-- Run in Supabase SQL Editor.

-- ============================================================
-- incidents — consolidation fields
-- ============================================================

ALTER TABLE incidents ADD COLUMN IF NOT EXISTS is_developing     BOOLEAN DEFAULT FALSE;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS update_count      INTEGER DEFAULT 0;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS source_timeline   JSONB DEFAULT '[]';
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS first_reported_at DATE;

-- ============================================================
-- incident_links
-- ============================================================

CREATE TABLE IF NOT EXISTS incident_links (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  incident_a      UUID NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
  incident_b      UUID NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
  link_type       TEXT NOT NULL CHECK (link_type IN ('related', 'follow_up', 'same_location')),
  confidence      DECIMAL(3,2) NOT NULL,
  agent_reason    TEXT NOT NULL,
  confirmed_by_operator BOOLEAN DEFAULT FALSE,
  rejected_by_operator  BOOLEAN DEFAULT FALSE,
  UNIQUE(incident_a, incident_b)
);

CREATE INDEX IF NOT EXISTS idx_links_incident_a ON incident_links(incident_a);
CREATE INDEX IF NOT EXISTS idx_links_incident_b ON incident_links(incident_b);
CREATE INDEX IF NOT EXISTS idx_links_pending ON incident_links(confirmed_by_operator, rejected_by_operator) WHERE confirmed_by_operator = FALSE AND rejected_by_operator = FALSE;

-- RLS for incident_links: public key can read confirmed links (shown on incident page)
ALTER TABLE incident_links ENABLE ROW LEVEL SECURITY;

CREATE POLICY "anon_read_confirmed_links" ON incident_links FOR SELECT TO anon USING (confirmed_by_operator = TRUE);

-- ============================================================
-- war_room_queue — expand status values + update column
-- ============================================================

ALTER TABLE war_room_queue DROP CONSTRAINT IF EXISTS war_room_queue_status_check;
ALTER TABLE war_room_queue ADD CONSTRAINT war_room_queue_status_check CHECK (status IN ('pending', 'approved', 'rejected', 'escalated', 'update', 'update_approved', 'update_rejected'));

ALTER TABLE war_room_queue ADD COLUMN IF NOT EXISTS update_target_incident_id UUID REFERENCES incidents(id);
