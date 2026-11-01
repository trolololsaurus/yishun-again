-- 026: a queue row claimed for publishing but not yet published.
--
-- Telegram Approve claims the row BEFORE calling _publish so a double tap cannot
-- publish twice. It used to claim to 'approved_pending', which is not in the
-- status CHECK below, so every claim failed and every Telegram Approve was a
-- silent no-op. The first fix reused the terminal 'approved' instead, which hides
-- a row that crashes mid-publish (status 'approved', no incident, off every
-- pending view). This adds the intended status so a stale claim can be found and
-- released: ops/auto_publish.py::release_stale_claims.
--
-- Additive: only widens the CHECK. Apply in the Supabase SQL Editor BEFORE
-- deploying the agents revision that writes 'approved_pending'; until it is
-- applied that revision cannot claim a row (same failure mode as 009/011).

ALTER TABLE war_room_queue DROP CONSTRAINT IF EXISTS war_room_queue_status_check;
ALTER TABLE war_room_queue ADD  CONSTRAINT war_room_queue_status_check
  CHECK (status IN ('pending', 'approved', 'rejected', 'escalated',
                    'update', 'update_approved', 'update_rejected',
                    'update_reverted', 'approved_pending'));
