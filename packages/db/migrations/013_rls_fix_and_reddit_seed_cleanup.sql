-- Migration 013: RLS fix for pattern_alerts/people_profiles + reddit seed cleanup
-- (July-2026 tech-lead security audit.)
-- Run in the Supabase SQL Editor after 012. Idempotent.

-- ============================================================
-- 1. pattern_alerts / people_profiles — remove the anon full-access hole.
--
-- Migration 003 created these policies with no TO clause and
-- USING (true) WITH CHECK (true):
--   CREATE POLICY "operator_full_access" ON pattern_alerts ...
--   CREATE POLICY "operator_only"        ON people_profiles ...
-- Without TO, a policy applies to PUBLIC (including anon); without FOR, to
-- ALL commands. Despite their names, anyone holding the publishable key
-- could read unpublished people profiles (names, aliases, legal_sensitivity)
-- and INSERT/UPDATE/DELETE rows in both tables.
--
-- The War Room and agents use the secret key, which bypasses RLS — these
-- policies served no legitimate caller. Dropping them (leaving RLS enabled
-- with no policy) locks both tables to service_role only, matching every
-- other private table (011 §9).
-- ============================================================
DROP POLICY IF EXISTS "operator_full_access" ON pattern_alerts;
DROP POLICY IF EXISTS "operator_only"        ON people_profiles;

-- Belt and braces in case 003 was ever applied twice under variant names:
-- verify with
--   SELECT policyname, tablename FROM pg_policies
--   WHERE tablename IN ('pattern_alerts', 'people_profiles');
-- which must return 0 rows after this migration.

-- ============================================================
-- 2. Guardrail #2 cleanup — reddit URLs quoted as sources in the 005 seeds.
--
-- 005 seeded two hero incidents with a reddit thread inside source_urls and
-- counted it in corroboration_count. Since 012 reclassified reddit as
-- type='signal', those rows quote a signal source on published incidents
-- (guardrail #2: signal sources are never in source_urls). 012 changed the
-- classification of future URLs only; this cleans the live rows. The 005
-- file itself is fixed in the same commit so a rebuild-from-migrations does
-- not reintroduce the breach.
-- ============================================================
UPDATE incidents
SET source_urls         = array_remove(source_urls, 'https://www.reddit.com/r/singapore/comments/kurt_tay_void_deck_fight_yishun_2022'),
    corroboration_count = GREATEST(1, corroboration_count - 1),
    hype_meter          = GREATEST(1, hype_meter - 1)
WHERE slug = 'kurt-tay-void-deck-fight-yishun-2022'
  AND 'https://www.reddit.com/r/singapore/comments/kurt_tay_void_deck_fight_yishun_2022' = ANY(source_urls);

UPDATE incidents
SET source_urls         = array_remove(source_urls, 'https://www.reddit.com/r/singapore/comments/japanese_youtuber_visits_yishun_2023'),
    corroboration_count = GREATEST(1, corroboration_count - 1)
WHERE slug = 'japanese-youtuber-visits-yishun-2023'
  AND 'https://www.reddit.com/r/singapore/comments/japanese_youtuber_visits_yishun_2023' = ANY(source_urls);
