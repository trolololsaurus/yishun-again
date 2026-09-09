-- 025_image_undo.sql
--
-- Short-lived image history for operator rectification (Track B, B4b).
--
-- ⚠️ HAND-APPLIED in the Supabase SQL Editor. There is no migration runner
--    (QA M15). Apply after 024.
--
-- ⚠️ LOAD-BEARING: the /rectify success write and the /revert-image route both
--    read and write image_history, and /rectify SELECTs it. Until this runs,
--    PostgREST rejects the write with "column does not exist" and rectification
--    fails — same failure mode as 009.
--
-- What this is:
--
--   Regeneration used to overwrite the image in place at a stable R2 key
--   (pixel-art/{slug}.png), so an operator who re-rolled an image they liked had
--   no way back — the bytes were gone. The fix keeps each superseded image alive
--   at its OWN versioned key (pixel-art/{slug}--{ms}.png) and lists it here, so
--   "bring back a previous image" is a pointer swap, not a re-render.
--
--   The list is deliberately SHORT-LIVED: the War Room offers the newest few for
--   revert, and ops/image_cleanup.py (piggybacked on the twice-daily chain)
--   deletes both the R2 object and the entry once it is older than
--   IMAGE_HISTORY_TTL_HOURS (default 8) or beyond IMAGE_HISTORY_KEEP (default 3).
--   So storage stays negligible — a few hundred KB per recently-rectified
--   incident, gone within a day.
--
-- Shape: JSONB array, newest last, of
--   {key, url, prompt, created_at}
-- where `key` is the R2 object key (for deletion), `url` the public URL (for
-- display and revert), `prompt` the prompt that produced it, and `created_at`
-- the ISO instant it STOPPED being live (its TTL clock starts then).

-- Undo the columns from the pre-release shape of this migration, if it was
-- applied before the design changed to a capped, expiring history. Harmless if
-- they never existed.
ALTER TABLE public.incidents
  DROP COLUMN IF EXISTS previous_pixel_art_url,
  DROP COLUMN IF EXISTS previous_image_prompt;

ALTER TABLE public.incidents
  ADD COLUMN IF NOT EXISTS image_history JSONB NOT NULL DEFAULT '[]'::jsonb;

COMMENT ON COLUMN public.incidents.image_history IS
  'Superseded pixel-art versions kept briefly for operator revert (migration '
  '025). Array of {key, url, prompt, created_at}, newest last. Pruned by '
  'ops/image_cleanup.py past IMAGE_HISTORY_KEEP or IMAGE_HISTORY_TTL_HOURS.';
