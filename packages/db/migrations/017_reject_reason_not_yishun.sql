-- Migration 017: reject taxonomy — add 'not_yishun', add free-text reject_note.
-- Run in the Supabase SQL Editor after 016. Idempotent.
--
-- ============================================================
-- 1. 'not_yishun' — a distinct failure mode, not "noise"
-- ============================================================
-- The keyword filter matches a plain case-insensitive substring anywhere in an
-- article's title OR body, so a story can match on "Yishun" appearing in
-- passing and not be about Yishun at all. Live example from the 2026-08-03
-- pass: "280 motorcyclists inspected at Admiralty Road West, 16 traffic
-- violations" — surfaced by the_independent_search because the body mentions
-- Yishun once.
--
-- Until now the operator had to file those as 'noise', which conflates two
-- failures that are fixed in completely different places:
--
--   noise       -> the story is junk. A Stage 1/Stage 2 prompt problem.
--   not_yishun  -> the story is fine, it is just not ours. A SOURCE problem —
--                  a cluster of these against one discovered_via means that
--                  adapter's query is too loose, and no prompt change fixes it.
--
-- Keeping them apart is what makes the second one actionable.

ALTER TABLE training_signals DROP CONSTRAINT IF EXISTS training_signals_reject_reason_check;
ALTER TABLE training_signals ADD CONSTRAINT training_signals_reject_reason_check
  CHECK (reject_reason IS NULL OR reject_reason IN (
    'noise',
    'duplicate',
    'unverified',
    'too_thin',
    'legal_risk',
    'not_yishun'      -- added 2026-08-03
  ));

-- ============================================================
-- 2. reject_note — free text for humans, NEVER for the prompt
-- ============================================================
-- Deliberately a SEPARATE column rather than free text in reject_reason.
--
-- `ingestion/learning.py::recent_examples` interpolates reject_reason VERBATIM
-- into the Stage 2 prompt ("operator REJECTED as '<reason>'") and round-robins
-- examples BY REASON, bucketing on the exact string — so that eight examples
-- teach eight lessons instead of teaching one lesson eight times.
--
-- Free text in that column would give every unique phrasing its own bucket of
-- one ("not yishun", "wrong town", "sembawang lah"), collapsing the round-robin
-- into "the 8 most recent" and destroying the diversity it exists to provide.
--
-- So: the enum above is the machine-readable signal; this column is for the
-- operator's own forensics. It also gives the taxonomy a promotion path — when
-- twenty notes rhyme, that is the evidence to make it a new enum value.
ALTER TABLE training_signals ADD COLUMN IF NOT EXISTS reject_note TEXT;

COMMENT ON COLUMN training_signals.reject_note IS
  'Operator free text. NOT read by the learning loop — see ingestion/learning.py; '
  'reject_reason is the machine-readable signal. Used for human review and as '
  'evidence for promoting a recurring note into a reject_reason enum value.';

-- Verify:
--   SELECT reject_reason, count(*) FROM training_signals
--   WHERE decision = 'reject' GROUP BY reject_reason ORDER BY 2 DESC;
