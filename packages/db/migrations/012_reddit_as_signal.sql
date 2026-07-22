-- ============================================================================
-- 012 — Reddit is a signal, not a source
--
-- Operator decision (July 2026): reddit is user-generated discussion, not
-- verifiable journalism. Its URL must never be a quoted source (guardrail #2),
-- and its post date is not an event date — a thread reviving an old case
-- carries a recent post date, which manufactured duplicate "new" cards for old
-- events. MSM is the sole authority for both the citation and the event date.
--
-- The pipeline already enforces this from the code change alone: scrape_reddit
-- emits source_type='signal', so is_signal_source() strips the URL from
-- source_urls in the orchestrator before it can be persisted. THIS migration is
-- the defensive third layer — it flips the two reddit rows in `sources` to
-- type='signal' so that classifiers.source_allowlist.classify() also resolves a
-- reddit domain to 'signal'. That catches the edge case where a reddit URL is
-- cited inside ANOTHER source's article and reaches check_source_urls by a path
-- that never saw the candidate's source_type.
--
-- Apply by hand in the Supabase SQL Editor, after 011. Idempotent.
--
-- Note: the two rows are scrape TARGETS (the r/singapore and r/singaporeraw
-- search feeds). Scraping is driven by the hardcoded get_enabled_sources()
-- list, not by sources.type, so this does NOT stop reddit being scraped — it
-- only changes how a reddit URL is CLASSIFIED and how the row renders in the
-- War Room sources admin.
-- ============================================================================

UPDATE sources
SET    type = 'signal'
WHERE  type = 'reddit'
   OR  url ILIKE '%reddit.com%';

-- Sanity: no scrape target should still be typed 'reddit'.
-- SELECT name, url, type FROM sources WHERE type = 'reddit';   -- expect 0 rows
