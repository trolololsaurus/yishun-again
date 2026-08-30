-- Migration 021: fix stale "email provider" comment on notifications.status.
-- Run in the Supabase SQL Editor after 020. Idempotent (comments only, no
-- schema/data change).
--
-- Migration 011 documented notifications.status as "disabled = no email
-- provider configured" — accurate when it was written, but the alerting
-- transport was swapped from Resend/email to Telegram on 2026-08-27
-- (ops/notify.py). The table, columns and CHECK constraint are unchanged;
-- only the column comment was left describing a transport that no longer
-- exists. Historical migration files are never rewritten (011 stays as the
-- record of what was true then) — this corrects the LIVE comment instead.

COMMENT ON COLUMN notifications.status IS 'disabled = no Telegram bot token/chat id configured; the alert is still recorded and visible in War Room.';

COMMENT ON TABLE notifications IS 'Outbound operator alert log + dedup ledger (Telegram since 2026-08-27; Resend/email before that — see ops/notify.py).';

COMMENT ON COLUMN notifications.recipient IS 'Telegram chat/user id the alert was addressed to.';
