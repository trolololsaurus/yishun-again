-- Migration 010: QA hardening (June-2026 full-codebase review)
-- Bundles the DB-level fixes from docs/QA_BACKLOG.md: C4, C3, M7.
-- Run in the Supabase SQL Editor after 009. Idempotent.

-- ============================================================
-- C4 — `source_urls ≥ 1` guardrail was broken for empty arrays.
-- array_length('{}', 1) returns NULL (not 0), so '{}' passed the old CHECK.
-- cardinality('{}') returns 0, which correctly fails >= 1.
-- (Audit first: SELECT id, slug FROM incidents WHERE cardinality(source_urls) < 1;)
-- ============================================================
ALTER TABLE incidents DROP CONSTRAINT IF EXISTS incidents_source_urls_check;
ALTER TABLE incidents DROP CONSTRAINT IF EXISTS incidents_source_urls_nonempty;
ALTER TABLE incidents ADD CONSTRAINT incidents_source_urls_nonempty
  CHECK (cardinality(source_urls) >= 1);

-- ============================================================
-- C3 — UTM analytics inserts were blocked: utm_events has RLS on with no
-- INSERT policy, and the public web app writes via the anon key. Add a
-- tightly-scoped anon INSERT policy (no SELECT — anon still cannot read).
-- utm_events stores no PII (hashed UA + Cloudflare geo only), so a permissive
-- WITH CHECK is acceptable; the /api/utm/log route is rate-limited.
-- ============================================================
DROP POLICY IF EXISTS "anon_insert_utm_events" ON utm_events;
CREATE POLICY "anon_insert_utm_events"
  ON utm_events
  FOR INSERT
  TO anon
  WITH CHECK (true);

-- ============================================================
-- M7 — war_room_queue → incidents FKs had no ON DELETE, so deleting an
-- incident referenced by a queue row was blocked (RESTRICT). Switch both to
-- SET NULL (mirrors utm_events.incident_id; preserves queue history).
-- ============================================================
ALTER TABLE war_room_queue DROP CONSTRAINT IF EXISTS war_room_queue_incident_id_fkey;
ALTER TABLE war_room_queue ADD CONSTRAINT war_room_queue_incident_id_fkey
  FOREIGN KEY (incident_id) REFERENCES incidents (id) ON DELETE SET NULL;

ALTER TABLE war_room_queue DROP CONSTRAINT IF EXISTS war_room_queue_update_target_incident_id_fkey;
ALTER TABLE war_room_queue ADD CONSTRAINT war_room_queue_update_target_incident_id_fkey
  FOREIGN KEY (update_target_incident_id) REFERENCES incidents (id) ON DELETE SET NULL;
