-- Migration 001: Initial Schema
-- Yishun Again — all tables, indexes, RLS policies, and seed data
-- Run in Supabase SQL Editor or via CLI: supabase db push

-- ============================================================
-- 3.1  incidents
-- ============================================================

CREATE TABLE incidents (
  id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  published_at        TIMESTAMPTZ,
  incident_date       DATE        NOT NULL,
  title               TEXT        NOT NULL,
  summary             TEXT        NOT NULL,
  classification      TEXT        NOT NULL
                        CHECK (classification IN ('heart', 'clown', 'dagger', 'custom')),
  custom_label        TEXT,
  severity            INTEGER     NOT NULL
                        CHECK (severity BETWEEN 1 AND 5),
  block_number        TEXT,
  area_name           TEXT,
  latitude            DECIMAL(9,6),
  longitude           DECIMAL(9,6),
  -- Legal guardrail §13.1: at least one source URL required
  source_urls         TEXT[]      NOT NULL
                        CHECK (array_length(source_urls, 1) >= 1),
  corroboration_count INTEGER     NOT NULL DEFAULT 1,
  edmw_signal_count   INTEGER     NOT NULL DEFAULT 0,
  hype_meter          INTEGER     NOT NULL DEFAULT 0
                        CHECK (hype_meter BETWEEN 0 AND 5),
  pixel_art_url       TEXT,
  share_card_url      TEXT,
  slug                TEXT        NOT NULL UNIQUE,
  seo_title           TEXT,
  seo_description     TEXT,
  is_published        BOOLEAN     NOT NULL DEFAULT FALSE,
  chaos_contribution  DECIMAL(4,2),
  agent_confidence    DECIMAL(3,2),
  tags                TEXT[]
);

CREATE INDEX idx_incidents_published       ON incidents (is_published, published_at DESC);
CREATE INDEX idx_incidents_classification  ON incidents (classification);
CREATE INDEX idx_incidents_location        ON incidents (latitude, longitude)
  WHERE latitude IS NOT NULL;
CREATE INDEX idx_incidents_date            ON incidents (incident_date DESC);

-- ============================================================
-- 3.2  sources
-- ============================================================

CREATE TABLE sources (
  id                       UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  name                     TEXT        NOT NULL UNIQUE,
  url                      TEXT        NOT NULL,
  type                     TEXT        NOT NULL
                             CHECK (type IN ('msm', 'reddit', 'signal', 'reference')),
  is_active                BOOLEAN     NOT NULL DEFAULT TRUE,
  scrape_interval_minutes  INTEGER     NOT NULL DEFAULT 60,
  reliability_score        DECIMAL(3,2) NOT NULL DEFAULT 0.70,
  added_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  approved_by_operator     BOOLEAN     NOT NULL DEFAULT FALSE,
  discovery_notes          TEXT
);

-- ============================================================
-- 3.3  utm_events
-- ============================================================

CREATE TABLE utm_events (
  id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  incident_id     UUID        REFERENCES incidents (id) ON DELETE SET NULL,
  timestamp       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  utm_source      TEXT,
  utm_medium      TEXT,
  utm_campaign    TEXT,
  geo_country     TEXT,
  geo_city        TEXT,
  geo_region      TEXT,
  vpn_suspected   BOOLEAN     NOT NULL DEFAULT FALSE,
  user_agent_hash TEXT,
  referrer        TEXT
);

CREATE INDEX idx_utm_incident  ON utm_events (incident_id);
CREATE INDEX idx_utm_timestamp ON utm_events (timestamp DESC);
CREATE INDEX idx_utm_source    ON utm_events (utm_source);

-- ============================================================
-- 3.4  training_signals
-- ============================================================

CREATE TABLE training_signals (
  id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  incident_id             UUID        REFERENCES incidents (id) ON DELETE CASCADE,
  timestamp               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  action                  TEXT        NOT NULL
                            CHECK (action IN ('approve', 'edit_approve', 'reject')),
  -- reject_reason is nullable; when present it must be one of the allowed values
  reject_reason           TEXT
                            CHECK (reject_reason IS NULL
                                   OR reject_reason IN ('noise', 'duplicate', 'unverified', 'too_thin', 'legal_risk')),
  original_draft          TEXT,
  edited_draft            TEXT,
  original_classification TEXT,
  edited_classification   TEXT,
  original_severity       INTEGER,
  edited_severity         INTEGER,
  operator_changes        JSONB,
  agent_confidence_was    DECIMAL(3,2)
);

CREATE INDEX idx_training_action    ON training_signals (action);
CREATE INDEX idx_training_timestamp ON training_signals (timestamp DESC);

-- ============================================================
-- 3.5  war_room_queue
-- ============================================================

CREATE TABLE war_room_queue (
  id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  raw_content             JSONB       NOT NULL,
  source_url              TEXT        NOT NULL,
  source_type             TEXT        NOT NULL,
  proposed_classification TEXT,
  proposed_severity       INTEGER,
  proposed_summary        TEXT,
  proposed_title          TEXT,
  proposed_pixel_prompt   TEXT,
  proposed_slug           TEXT,
  agent_confidence        DECIMAL(3,2),
  corroboration_count     INTEGER     NOT NULL DEFAULT 1,
  edmw_signal_count       INTEGER     NOT NULL DEFAULT 0,
  status                  TEXT        NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending', 'approved', 'rejected', 'escalated')),
  processed_at            TIMESTAMPTZ,
  incident_id             UUID        REFERENCES incidents (id)
);

CREATE INDEX idx_queue_status ON war_room_queue (status, created_at DESC);

-- ============================================================
-- 3.6  chaos_index_snapshots
-- ============================================================

CREATE TABLE chaos_index_snapshots (
  id                 UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  snapshot_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  score_30d          DECIMAL(5,2),
  score_90d          DECIMAL(5,2),
  score_365d         DECIMAL(5,2),
  score_alltime      DECIMAL(5,2),
  descriptor         TEXT,
  incident_count_30d INTEGER,
  dagger_count_30d   INTEGER,
  clown_count_30d    INTEGER,
  heart_count_30d    INTEGER
);

-- ============================================================
-- ROW LEVEL SECURITY
-- All writes use SUPABASE_SECRET_KEY (service_role) which bypasses
-- RLS entirely. Public policies below govern the anon/publishable key.
-- ============================================================

ALTER TABLE incidents            ENABLE ROW LEVEL SECURITY;
ALTER TABLE sources              ENABLE ROW LEVEL SECURITY;
ALTER TABLE utm_events           ENABLE ROW LEVEL SECURITY;
ALTER TABLE training_signals     ENABLE ROW LEVEL SECURITY;
ALTER TABLE war_room_queue       ENABLE ROW LEVEL SECURITY;
ALTER TABLE chaos_index_snapshots ENABLE ROW LEVEL SECURITY;

-- incidents: public key can only read published incidents
CREATE POLICY "anon_read_published_incidents"
  ON incidents
  FOR SELECT
  TO anon
  USING (is_published = TRUE);

-- chaos_index_snapshots: public key can read (shown on homepage)
CREATE POLICY "anon_read_chaos_snapshots"
  ON chaos_index_snapshots
  FOR SELECT
  TO anon
  USING (TRUE);

-- sources, utm_events, training_signals, war_room_queue:
-- no anon access — service_role only (War Room + agents backend)

-- ============================================================
-- SEED DATA — sources (§3.2)
-- Wikipedia scrape_interval = 0 because it is never scheduled;
-- only queried during operator-initiated backfill runs.
-- ============================================================

INSERT INTO sources (name, url, type, scrape_interval_minutes, approved_by_operator) VALUES
  -- English MSM
  ('CNA',                       'https://www.channelnewsasia.com',                       'msm',       60,  TRUE),
  ('The Straits Times',          'https://www.straitstimes.com',                          'msm',       60,  TRUE),
  ('Mothership',                 'https://mothership.sg',                                 'msm',       60,  TRUE),
  ('Stomp',                      'https://stomp.straitstimes.com',                        'msm',      120,  TRUE),
  ('MustShareNews',              'https://mustsharenews.com',                             'msm',       60,  TRUE),
  ('The Independent Singapore',  'https://theindependent.sg',                             'msm',       60,  TRUE),
  ('Jom',                        'https://jom.media',                                     'msm',      360,  TRUE),
  -- Aggregators
  ('Yahoo News Singapore',       'https://sg.news.yahoo.com',                             'msm',      120,  TRUE),
  ('AsiaOne',                    'https://www.asiaone.com',                               'msm',      120,  TRUE),
  -- Multilingual MSM (content translated to English by Stage 2 agent)
  ('Lianhe Zaobao',              'https://www.zaobao.com.sg',                             'msm',      180,  TRUE),
  ('Shin Min Daily News',        'https://www.shinmin.sg',                                'msm',      180,  TRUE),
  ('Berita Harian',              'https://www.beritaharian.sg',                           'msm',      180,  TRUE),
  ('Tamil Murasu',               'https://tamilmurasu.com.sg',                            'msm',      180,  TRUE),
  -- Reddit
  ('Reddit Singapore',           'https://www.reddit.com/r/singapore',                   'reddit',    30,  TRUE),
  ('Reddit SingaporeRaw',        'https://www.reddit.com/r/singaporeraw',                'reddit',    30,  TRUE),
  -- Signal only — EDMW content is never quoted or attributed directly as a source
  ('HWZ EDMW',                   'https://forums.hardwarezone.com.sg/eat-drink-man-woman-16', 'signal', 60, TRUE),
  -- Reference — one-off backfill enrichment only, never on a scraping schedule
  ('Wikipedia',                  'https://en.wikipedia.org',                             'reference',  0,  TRUE);
