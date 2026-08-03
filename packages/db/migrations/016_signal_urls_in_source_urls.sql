-- Migration 016: guardrail #2 cleanup — signal URLs quoted as sources.
-- Run in the Supabase SQL Editor after 015. Idempotent.
--
-- Audit 2026-08-03 found FOUR published incidents carrying a reddit thread
-- inside `incidents.source_urls`, each counted in `corroboration_count`:
--
--   yishun-group-knife-attack-block-243-carpark-jul-2026        3 urls, 2 msm
--   yishun-man-found-dead-void-deck-block-128-jul-2026          2 urls, 1 msm
--   yishun-public-nuisance-spitting-police-firefighting-jul-2026 10 urls, 9 msm
--   yishun-remote-gambling-bust-17-arrested-jul-2026            1 url,  0 msm  <-- see §2
--
-- Guardrail #2: sources with type='signal' (EDMW/HWZ and, since July 2026,
-- Reddit) are NEVER included in source_urls. Migration 013 cleaned the two
-- reddit URLs that migration 005 had seeded; these four arrived later, through
-- the live pipeline, so 013's slug-specific UPDATEs could not have caught them.
-- This migration is written against the DOMAIN instead of against slugs, so it
-- also cleans anything similar that lands before the ingestion-side fix does.
--
-- It also corrects the count. Each of these rows advertised a source total that
-- included the forum thread, which is exactly the "Corroborated by N sources"
-- number the public incident page prints.

-- ============================================================
-- 1. Strip signal URLs, correct the count, credit the forum buzz.
--
-- The cardinality(kept) >= 1 guard is guardrail #1 (migration 010's
-- CHECK (cardinality(source_urls) >= 1)): a row whose ONLY citation is a
-- signal URL is NOT silently emptied here — it cannot be, the CHECK would
-- reject the write and take the whole migration down with it. Such rows are
-- left untouched for §2.
-- ============================================================
WITH cleaned AS (
    SELECT
        id,
        source_urls AS old_urls,
        ARRAY(
            SELECT u FROM unnest(source_urls) AS u
            WHERE u !~* '^https?://([a-z0-9-]+\.)*(reddit\.com|hardwarezone\.com\.sg)(/|$)'
        ) AS kept
    FROM incidents
)
UPDATE incidents i
SET source_urls         = c.kept,
    corroboration_count = GREATEST(1, cardinality(c.kept)),
    -- The thread is still forum buzz, it is just not a citation.
    edmw_signal_count   = COALESCE(i.edmw_signal_count, 0)
                          + (cardinality(c.old_urls) - cardinality(c.kept))
FROM cleaned c
WHERE i.id = c.id
  AND cardinality(c.kept) < cardinality(c.old_urls)   -- there is a signal URL to remove
  AND cardinality(c.kept) >= 1;                       -- guardrail #1 — never empty the array

-- Verify (expect 0 rows):
--   SELECT slug, source_urls FROM incidents
--   WHERE EXISTS (SELECT 1 FROM unnest(source_urls) u
--                 WHERE u ~* '^https?://([a-z0-9-]+\.)*(reddit\.com|hardwarezone\.com\.sg)(/|$)')
--     AND cardinality(source_urls) > 1;

-- ============================================================
-- 2. OPERATOR DECISION — rows whose ONLY source is a signal URL.
--
-- `yishun-remote-gambling-bust-17-arrested-jul-2026` is published with a single
-- citation, and that citation is a reddit thread. It therefore breaches
-- guardrail #2 (a signal is quoted as a source) and, once the URL is removed,
-- guardrail #1 (no verifiable source at all). Per CLAUDE.md a signal-only
-- candidate "stays in the queue as unverified until an operator attaches an MSM
-- source; never auto-publishes" — this row should never have reached the site.
--
-- There is no correct automatic answer, so this migration does NOT act. Pick one
-- and uncomment it:
--
-- (a) Attach the real reporting and clean the row -- PREFERRED:
--       UPDATE incidents
--       SET source_urls         = ARRAY['<publisher URL for the 17-arrest gambling bust>'],
--           corroboration_count = 1,
--           edmw_signal_count   = COALESCE(edmw_signal_count, 0) + 1
--       WHERE slug = 'yishun-remote-gambling-bust-17-arrested-jul-2026';
--
-- (b) Unpublish until a source exists (the row survives; the card leaves the site):
--       UPDATE incidents
--       SET is_published = FALSE
--       WHERE slug = 'yishun-remote-gambling-bust-17-arrested-jul-2026';
--
-- Find any others in this state before deciding:
--   SELECT slug, source_urls FROM incidents
--   WHERE is_published
--     AND cardinality(source_urls) = 1
--     AND source_urls[1] ~* '^https?://([a-z0-9-]+\.)*(reddit\.com|hardwarezone\.com\.sg)(/|$)';
-- ============================================================
