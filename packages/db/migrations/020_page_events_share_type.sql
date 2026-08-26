-- Migration 020: share-click tracking on page_events.
-- Run in the Supabase SQL Editor after 019. Idempotent.
--
-- Reuses page_events rather than a new table — a share click is a distinct
-- EVENT on an already-tracked page, not a new kind of thing that needs its
-- own table/route/RLS setup. event_type defaults to 'pageview' so every
-- existing row (and every insert from the Chunk 2 pageview beacon) is
-- unaffected. Chunk 4's leaderboard/bounce queries must filter
-- event_type = 'pageview' when counting views/bounces — a 'share' row is not
-- a page load and would corrupt the bounce-rate math (session row count) if
-- counted as one.

ALTER TABLE page_events ADD COLUMN IF NOT EXISTS event_type TEXT NOT NULL DEFAULT 'pageview';

ALTER TABLE page_events DROP CONSTRAINT IF EXISTS page_events_event_type_check;
ALTER TABLE page_events ADD CONSTRAINT page_events_event_type_check
  CHECK (event_type IN ('pageview', 'share'));

CREATE INDEX IF NOT EXISTS idx_page_events_event_type ON page_events (event_type);

-- No RLS/grant changes needed: the existing anon_insert_page_events policy is
-- WITH CHECK (true), already permissive to any column value including this
-- new one. Share events never get a dwell fill, so the anon UPDATE path
-- (dwell_ms only, via the secret-key route) is untouched by this migration.
