-- 014_image_status.sql
--
-- Image generation state on published incidents (Track B, B5 + B4b).
--
-- ⚠️ HAND-APPLIED in the Supabase SQL Editor. There is no migration runner
--    (QA M15). Apply after 013.
--
-- Why a status and not just a null pixel_art_url:
--
--   A null URL currently means four different things — never attempted,
--   deliberately suppressed under guardrail #5, refused by the safety filter,
--   or failed transiently. The operator rectification queue (B4b) must show the
--   refusals and hide the suppressions, and a future backfill must retry the
--   failures and never retry the suppressions. Without this column both are
--   impossible, and a backfill would re-attempt every suppressed suicide story
--   on every pass forever.
--
-- Expand/contract: all three columns are nullable and existing rows are
-- backfilled to 'pending'. No CHECK is enforced yet — add it only once every
-- writer sets the column (ops/auto_publish.py and the War Room approve route
-- both do as of Track B B5).

ALTER TABLE public.incidents
  ADD COLUMN IF NOT EXISTS image_status   TEXT,
  ADD COLUMN IF NOT EXISTS image_prompt   TEXT,
  ADD COLUMN IF NOT EXISTS image_attempts JSONB;

COMMENT ON COLUMN public.incidents.image_status IS
  'ok | suppressed | refused | transient | invalid | skipped | pending | no_image_final. '
  'Terminal states a backfill must NEVER retry: suppressed, no_image_final.';
COMMENT ON COLUMN public.incidents.image_prompt IS
  'The full assembled prompt of the last attempt — what the operator edits when rectifying.';
COMMENT ON COLUMN public.incidents.image_attempts IS
  'Array of {n, prompt, outcome, reason} — what was tried and what was refused.';

-- Existing rows predate generation entirely.
UPDATE public.incidents
   SET image_status = CASE WHEN pixel_art_url IS NULL THEN 'pending' ELSE 'ok' END
 WHERE image_status IS NULL;

-- The rectification queue filters on this; without it the War Room view does a
-- sequential scan of every published incident.
CREATE INDEX IF NOT EXISTS idx_incidents_image_status
  ON public.incidents (image_status)
  WHERE image_status IN ('refused', 'transient', 'invalid', 'skipped');

-- ── Purge of SDXL-era stored prompts (Track B, B4) ──────────────────────────
--
-- ⚠️ SNAPSHOT FIRST. These prompts are the wrong FORMAT for Gemini but they are
--    a free corpus of what Stage 2 produced when it read an article, useful for
--    tuning the prose template and for the A5 eval set. Run this and keep the
--    output before executing the two statements below:
--
--      COPY (SELECT id, proposed_title, proposed_pixel_prompt
--              FROM war_room_queue
--             WHERE proposed_pixel_prompt IS NOT NULL
--               AND proposed_pixel_prompt <> '')
--        TO STDOUT WITH CSV HEADER;
--
-- Two locations per row, both written by consolidation/queue_row.py. Nulling
-- the column without stripping the JSONB key leaves the panel still rendering
-- stale content, so both statements are required.

UPDATE public.war_room_queue
   SET proposed_pixel_prompt = NULL
 WHERE proposed_pixel_prompt IS NOT NULL;

UPDATE public.war_room_queue
   SET raw_content = raw_content - 'pixel_art_prompt'
 WHERE raw_content ? 'pixel_art_prompt';
