-- 015 — constrain incidents.image_status to the known vocabulary
--
-- ⚠️ HAND-APPLIED in the Supabase SQL Editor. Apply AFTER 014.
--
-- ## Why
--
-- 014 added `image_status TEXT` with no CHECK. Three layers already agree on the
-- vocabulary and none of them is the one that persists:
--
--   apps/war-room/lib/types.ts   ImageStatus union + isImageStatus() guard
--   packages/agents/art/…        ImageResult.status docstring
--   the database                 accepts any string at all
--
-- So the only layer that outlives a deploy is the only one not enforcing. That
-- is the same shape as the 009 and 011 failures, inverted: there a missing CHECK
-- silently REJECTED valid writes; here a missing CHECK silently ACCEPTS invalid
-- ones. Both are quiet, and quiet is the problem.
--
-- This matters more than a typo would suggest, because two of the values are
-- terminal. `suppressed` and `no_image_final` are excluded from
-- RECTIFIABLE_STATUSES, so a row that reaches either is out of the operator
-- queue permanently and has no UI override. A bad value that merely LOOKS
-- terminal is unreachable by every code path that would fix it.
--
-- NULL stays legal: the column is nullable and pre-014 rows may still carry it.

-- ── Step 1: look before enforcing ───────────────────────────────────────────
-- Run this FIRST. Expect zero rows. Anything returned must be corrected by hand
-- before step 3 — do not guess a mapping, the value tells you which writer is
-- wrong.

SELECT image_status, count(*) AS rows
  FROM public.incidents
 WHERE image_status IS NOT NULL
   AND image_status NOT IN ('ok', 'suppressed', 'refused', 'transient',
                            'invalid', 'skipped', 'pending', 'no_image_final')
 GROUP BY image_status
 ORDER BY rows DESC;

-- ── Step 2: enforce for new writes, without blocking on history ─────────────
-- NOT VALID applies the constraint to every INSERT and UPDATE from now on but
-- skips the scan of existing rows, so this takes no table lock worth worrying
-- about on a live table.

ALTER TABLE public.incidents
  DROP CONSTRAINT IF EXISTS incidents_image_status_check;

ALTER TABLE public.incidents
  ADD CONSTRAINT incidents_image_status_check
  CHECK (
    image_status IS NULL
    OR image_status IN ('ok', 'suppressed', 'refused', 'transient',
                        'invalid', 'skipped', 'pending', 'no_image_final')
  ) NOT VALID;

-- ── Step 3: validate the back catalogue ─────────────────────────────────────
-- Only after step 1 returned nothing. If it did return rows, fix them first;
-- this statement fails loudly rather than silently dropping anything, which is
-- the intended behaviour.

ALTER TABLE public.incidents
  VALIDATE CONSTRAINT incidents_image_status_check;

-- ── Verify ──────────────────────────────────────────────────────────────────
-- Expect one row, convalidated = true.

SELECT conname, convalidated, pg_get_constraintdef(oid)
  FROM pg_constraint
 WHERE conrelid = 'public.incidents'::regclass
   AND conname  = 'incidents_image_status_check';
