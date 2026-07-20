-- ============================================================================
-- 011 — Autonomy & Ops schema
--
-- Everything the daily autonomous pass needs to be observable, accountable and
-- reversible:
--   1. agent_runs / agent_events   — activity log: success, failure, anomaly
--   2. notifications               — outbound email log + dedup ledger
--   3. learning_snapshots          — confidence/agreement delta over time
--   4. monthly_reports             — 30-day orchestrator summary, with history
--   5. backend_health_checks       — Supabase / R2 / Cloud Run / API / cost
--   6. training_signals.action    += 'auto_approve', 'auto_publish_reverted'
--   7. scraper_health + milestones — schema-of-record backfill (see note)
--   8. sources                     — retire the Jom row
--
-- Apply by hand in the Supabase SQL Editor, after 010. Idempotent: safe to
-- re-run. There is still no migration runner (QA M15).
--
-- NOTE on §7: scraper_health and milestones already exist in the live DB but
-- were never captured in packages/db/migrations — they lived only as DDL inside
-- the TechSpec. The CREATE TABLE IF NOT EXISTS blocks below are a no-op against
-- the live DB and exist so a fresh environment can be rebuilt from migrations
-- alone. Shapes mirror TechSpec v1.9 §8a / §13c exactly.
-- ============================================================================


-- ── 1. Agent activity log ───────────────────────────────────────────────────
-- One agent_runs row per agent invocation; many agent_events rows beneath it.
-- Every agent in packages/agents/ops/ writes here, so "what did the fleet do
-- last night" is one query, not a Cloud Run log trawl.

CREATE TABLE IF NOT EXISTS agent_runs (
  id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  agent        TEXT        NOT NULL,
  trigger      TEXT        NOT NULL DEFAULT 'scheduler'
                 CHECK (trigger IN ('scheduler', 'manual', 'chained')),
  started_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  finished_at  TIMESTAMPTZ,
  duration_ms  INTEGER,
  status       TEXT        NOT NULL DEFAULT 'running'
                 CHECK (status IN ('running', 'ok', 'degraded', 'failed')),
  summary      TEXT,
  stats        JSONB       NOT NULL DEFAULT '{}'::jsonb,
  error        TEXT
);

CREATE INDEX IF NOT EXISTS idx_agent_runs_agent   ON agent_runs (agent, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_runs_started ON agent_runs (started_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_runs_status  ON agent_runs (status, started_at DESC);

COMMENT ON TABLE  agent_runs IS 'One row per agent invocation. status=running means in flight or crashed mid-pass.';
COMMENT ON COLUMN agent_runs.stats IS 'Agent-specific counters, e.g. {"queued":4,"blocked":1}.';


CREATE TABLE IF NOT EXISTS agent_events (
  id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  run_id      UUID        REFERENCES agent_runs (id) ON DELETE CASCADE,
  agent       TEXT        NOT NULL,
  level       TEXT        NOT NULL
                CHECK (level IN ('info', 'success', 'warning', 'error', 'anomaly')),
  event       TEXT        NOT NULL,
  message     TEXT        NOT NULL,
  source_name TEXT,
  detail      JSONB       NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_agent_events_run     ON agent_events (run_id, created_at);
CREATE INDEX IF NOT EXISTS idx_agent_events_level   ON agent_events (level, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_events_created ON agent_events (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_events_source  ON agent_events (source_name, created_at DESC);

COMMENT ON COLUMN agent_events.event IS 'Stable machine code (source_blocked, stage1_halt, dupe_merged...). Filter on this, not on message.';
COMMENT ON COLUMN agent_events.level IS 'anomaly = needs a human eye but is not itself a crash.';


-- ── 2. Outbound notification ledger ─────────────────────────────────────────
-- Every email the system tries to send is a row here FIRST, then sent. So a
-- provider outage loses nothing, and dedup_key stops alert storms: the same
-- key inside its throttle window is written 'suppressed' instead of re-sent.

CREATE TABLE IF NOT EXISTS notifications (
  id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  kind        TEXT        NOT NULL
                CHECK (kind IN ('review_queue', 'anomaly', 'maintenance',
                                'health', 'monthly_report', 'test')),
  dedup_key   TEXT        NOT NULL,
  recipient   TEXT        NOT NULL,
  subject     TEXT        NOT NULL,
  body        TEXT        NOT NULL,
  status      TEXT        NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'sent', 'failed', 'suppressed', 'disabled')),
  provider_id TEXT,
  error       TEXT,
  sent_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_notifications_dedup   ON notifications (dedup_key, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notifications_created ON notifications (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notifications_status  ON notifications (status, created_at DESC);

COMMENT ON COLUMN notifications.status IS 'disabled = no email provider configured; the alert is still recorded and visible in War Room.';


-- ── 3. Learning delta tracking ──────────────────────────────────────────────
-- Answers "is the model actually learning, or stagnant?" — the whole point of
-- req #5. One snapshot per capture; the DELTA between snapshots is the signal,
-- so history is never pruned.

CREATE TABLE IF NOT EXISTS learning_snapshots (
  id                    UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  captured_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  window_days           INTEGER     NOT NULL DEFAULT 30,

  sample_count          INTEGER     NOT NULL DEFAULT 0,

  -- How confident the agent was, and which way that is trending
  mean_confidence       DECIMAL(4,3),
  mean_confidence_prev  DECIMAL(4,3),
  confidence_delta      DECIMAL(4,3),

  -- How often the operator agreed without changing anything. The real score.
  agreement_rate        DECIMAL(4,3),
  agreement_rate_prev   DECIMAL(4,3),
  agreement_delta       DECIMAL(4,3),

  edit_rate             DECIMAL(4,3),
  reject_rate           DECIMAL(4,3),

  -- Calibration: is high confidence actually earned?
  auto_publish_count    INTEGER     NOT NULL DEFAULT 0,
  auto_publish_reverted INTEGER     NOT NULL DEFAULT 0,

  verdict               TEXT        NOT NULL
                          CHECK (verdict IN ('learning', 'stagnant', 'regressing',
                                             'insufficient_data')),
  per_category          JSONB       NOT NULL DEFAULT '{}'::jsonb,
  notes                 TEXT
);

CREATE INDEX IF NOT EXISTS idx_learning_snapshots_captured ON learning_snapshots (captured_at DESC);

COMMENT ON TABLE  learning_snapshots IS 'Trend, not state. Two snapshots make a delta; one snapshot means nothing.';
COMMENT ON COLUMN learning_snapshots.agreement_rate IS 'approve-without-edits / all operator decisions in the window. Rising = learning.';
COMMENT ON COLUMN learning_snapshots.auto_publish_reverted IS 'Auto-published incidents the operator later unpublished. Rising = over-confident, tighten the gate.';
COMMENT ON COLUMN learning_snapshots.verdict IS 'stagnant = |agreement_delta| below the noise floor across the window.';


-- ── 4. Monthly orchestrator report ──────────────────────────────────────────

CREATE TABLE IF NOT EXISTS monthly_reports (
  id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  period_start DATE        NOT NULL,
  period_end   DATE        NOT NULL,
  report       JSONB       NOT NULL,
  summary_text TEXT        NOT NULL,
  emailed_at   TIMESTAMPTZ,
  CONSTRAINT monthly_reports_period_unique UNIQUE (period_start, period_end)
);

CREATE INDEX IF NOT EXISTS idx_monthly_reports_period ON monthly_reports (period_start DESC);

COMMENT ON TABLE monthly_reports IS 'Generated on the 1st for the preceding 30 days. UNIQUE(period) makes regeneration an upsert, not a duplicate.';


-- ── 5. Backend health + cost ────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS backend_health_checks (
  id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  component  TEXT        NOT NULL,
  status     TEXT        NOT NULL
               CHECK (status IN ('ok', 'degraded', 'down', 'unknown')),
  latency_ms INTEGER,
  message    TEXT,
  detail     JSONB       NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_backend_health_checked   ON backend_health_checks (checked_at DESC);
CREATE INDEX IF NOT EXISTS idx_backend_health_component ON backend_health_checks (component, checked_at DESC);

COMMENT ON COLUMN backend_health_checks.component IS 'supabase | cloudflare_r2 | cloud_run | gemini | anthropic | cost_guard';


-- ── 6. training_signals: record autonomous decisions ────────────────────────
-- An auto-approval is still a training signal — it is the agent grading its own
-- homework, and the operator's later unpublish is the correction. Without these
-- values in the CHECK, every autonomous decision insert is silently rejected —
-- exactly the bug migration 009 existed to fix for 'unpublish'.

ALTER TABLE training_signals DROP CONSTRAINT IF EXISTS training_signals_action_check;
ALTER TABLE training_signals ADD  CONSTRAINT training_signals_action_check
  CHECK (action IN ('approve', 'edit_approve', 'reject',
                    'pattern_confirmed', 'pattern_dismissed',
                    'unpublish',
                    'auto_approve', 'auto_publish_reverted'));

ALTER TABLE training_signals DROP CONSTRAINT IF EXISTS training_signals_decision_check;
ALTER TABLE training_signals ADD  CONSTRAINT training_signals_decision_check
  CHECK (decision IN ('approve', 'reject', 'approve_with_edits',
                      're_source', 'link_umbrella', 'escalate',
                      'auto_approve'));

-- Distinguishes an agent decision from an operator decision. Without it the
-- agreement rate in learning_snapshots would count the agent's own
-- auto-approvals as operator agreement and read as 100% forever.
ALTER TABLE training_signals ADD COLUMN IF NOT EXISTS decided_by TEXT NOT NULL DEFAULT 'operator';
ALTER TABLE training_signals DROP CONSTRAINT IF EXISTS training_signals_decided_by_check;
ALTER TABLE training_signals ADD  CONSTRAINT training_signals_decided_by_check
  CHECK (decided_by IN ('operator', 'agent'));

CREATE INDEX IF NOT EXISTS idx_training_decided_by ON training_signals (decided_by, created_at DESC);

COMMENT ON COLUMN training_signals.decided_by IS 'agent = autonomous decision (auto-publish). Excluded from operator agreement-rate maths.';

-- The 001 index still points at the pre-006 column name.
DROP   INDEX IF EXISTS idx_training_timestamp;
CREATE INDEX IF NOT EXISTS idx_training_created_at ON training_signals (created_at DESC);


-- ── 7. Schema-of-record backfill: scraper_health, milestones ────────────────
-- Both already exist live. Present here so migrations alone can rebuild the DB.

CREATE TABLE IF NOT EXISTS scraper_health (
  id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  scraped_at        TIMESTAMPTZ DEFAULT NOW(),
  source_name       TEXT        NOT NULL,
  source_type       TEXT        NOT NULL,
  items_found       INTEGER     DEFAULT 0,
  items_passed_s1   INTEGER     DEFAULT 0,
  errors            TEXT[],
  duration_ms       INTEGER,
  status            TEXT        NOT NULL CHECK (status IN ('ok', 'warning', 'error')),
  status_reason     TEXT,
  consecutive_zeros INTEGER     DEFAULT 0,
  avg_duration_7d   INTEGER
);

CREATE INDEX IF NOT EXISTS idx_health_source ON scraper_health (source_name, scraped_at DESC);
CREATE INDEX IF NOT EXISTS idx_health_status ON scraper_health (status, scraped_at DESC);

CREATE TABLE IF NOT EXISTS milestones (
  id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at     TIMESTAMPTZ DEFAULT NOW(),
  type           TEXT        NOT NULL,
  value          INTEGER     NOT NULL,
  incident_id    UUID        REFERENCES incidents (id),
  triggered_by   UUID        REFERENCES incidents (id),
  triggered_date DATE        NOT NULL,
  source_url     TEXT        NOT NULL,
  notified_at    TIMESTAMPTZ
);


-- ── 8. Retire the Jom source ────────────────────────────────────────────────
-- Dropped back in v1.4 (SSL failures + arts/culture focus, near-zero Yishun
-- relevance) but the seed row from 001 was never removed, so it still showed up
-- in the War Room sources list as an active scrape target that nothing scrapes.

DELETE FROM sources WHERE name = 'Jom' OR url ILIKE '%jom.media%';


-- ── 9. RLS — service_role only, matching every other ops table ──────────────
-- No anon policies: none of these are public reads. War Room uses the secret
-- key, so it is unaffected.

ALTER TABLE agent_runs            ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_events          ENABLE ROW LEVEL SECURITY;
ALTER TABLE notifications         ENABLE ROW LEVEL SECURITY;
ALTER TABLE learning_snapshots    ENABLE ROW LEVEL SECURITY;
ALTER TABLE monthly_reports       ENABLE ROW LEVEL SECURITY;
ALTER TABLE backend_health_checks ENABLE ROW LEVEL SECURITY;
ALTER TABLE scraper_health        ENABLE ROW LEVEL SECURITY;
ALTER TABLE milestones            ENABLE ROW LEVEL SECURITY;
