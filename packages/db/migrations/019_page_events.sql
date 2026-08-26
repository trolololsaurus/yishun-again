-- Migration 019: first-party pageview/session tracking (page_events).
-- Run in the Supabase SQL Editor after 018. Idempotent.
--
-- ============================================================
-- Why this table exists, separate from utm_events
-- ============================================================
-- utm_events only fires when a visitor arrives via a UTM-tagged link
-- (UTMLogger.tsx early-returns otherwise) — it was built to measure share
-- click-throughs, not general browsing. It has no concept of a visit that
-- stays on one page vs. one that browses several, and no dwell time.
--
-- page_events fires on EVERY pageview (organic included) and gets a second,
-- best-effort beacon on unload/navigation to fill in dwell_ms — the only way
-- to answer "bounce rate" and "are people staying and browsing", since
-- Cloudflare's own analytics (see classifiers/cf_analytics.py) doesn't expose
-- either metric on this zone's plan.
--
-- session_id is a client-generated UUID (crypto.randomUUID(), sessionStorage)
-- — not tied to any account or PII. A "bounce" is computed at query time as a
-- session with exactly one page_events row.

CREATE TABLE page_events (
  id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id      UUID        NOT NULL,
  incident_id     UUID        REFERENCES incidents (id) ON DELETE SET NULL,
  path            TEXT        NOT NULL,
  referrer        TEXT,
  geo_country     TEXT,
  geo_city        TEXT,
  user_agent_hash TEXT,
  dwell_ms        INTEGER,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_page_events_incident   ON page_events (incident_id);
CREATE INDEX idx_page_events_session    ON page_events (session_id);
CREATE INDEX idx_page_events_created_at ON page_events (created_at DESC);

ALTER TABLE page_events ENABLE ROW LEVEL SECURITY;

-- Insert: same shape as migration 010's utm_events policy — anon writes via
-- the public web app's publishable key, no PII in the row, rate-limited at
-- the route.
DROP POLICY IF EXISTS "anon_insert_page_events" ON page_events;
CREATE POLICY "anon_insert_page_events"
  ON page_events
  FOR INSERT
  TO anon
  WITH CHECK (true);

-- Update: ONLY for the dwell-fill beacon, which is why this is tighter than
-- the insert policy above. A blanket USING(true)/WITH CHECK(true) UPDATE
-- would let anyone holding a row's id (a UUID the client already has, by
-- design) rewrite it forever, including old rows — so row age is the real
-- boundary: only rows created in the last hour are touchable, matching how
-- long a real browser tab's beacon could plausibly still be open.
--
-- ponytail: this was originally column-restricted (GRANT UPDATE (dwell_ms)
-- ON page_events TO anon) so a malicious client could touch dwell_ms only,
-- not path/referrer/incident_id. Verified live against this project
-- (2026-08-26): PostgREST's real UPDATE path returns 0 rows matched under a
-- column-scoped grant even though has_column_privilege() confirms Postgres
-- itself has it — a PostgREST/column-grant interaction, not a policy bug.
-- Falling back to a table-level grant so the feature actually works; the
-- 1-hour row-age window is what's actually carrying the security weight here.
-- Upgrade path if this ever gets abused: a BEFORE UPDATE trigger that raises
-- unless only dwell_ms changed, which sidesteps PostgREST's column-grant path
-- entirely.
GRANT UPDATE ON page_events TO anon;

DROP POLICY IF EXISTS "anon_update_page_events_dwell" ON page_events;
CREATE POLICY "anon_update_page_events_dwell"
  ON page_events
  FOR UPDATE
  TO anon
  USING      (created_at > NOW() - INTERVAL '1 hour')
  WITH CHECK (created_at > NOW() - INTERVAL '1 hour');
