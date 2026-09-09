"""
Image history cleanup (Track B, B4b — migration 025).

Operator rectification keeps each superseded pixel-art version alive at its own
R2 key so the operator can revert to it (incidents.image_history). Those are
meant to be SHORT-LIVED. This sweep, piggybacked on the twice-daily chain,
deletes both the R2 object and the history entry once it is either older than
IMAGE_HISTORY_TTL_HOURS (default 8) or beyond the newest IMAGE_HISTORY_KEEP
(default 3). Effective lifetime is ~8-20h given the ~12h gap between passes —
the operator accepted that in exchange for no dedicated cron.

The current live image is NEVER in image_history (it is incidents.pixel_art_url),
so this can never delete the picture a page is actually serving.

Never raises (ops rule): a cleanup crash must not cost the pass. Honours
dry_run — it reports what it WOULD expire and touches neither R2 nor the DB.

Public API
----------
run(supabase_client=None, trigger="scheduler", dry_run=False, now=None) -> dict
    {"incidents_scanned", "entries_expired", "objects_deleted", "errors", ...}
"""

import logging
import os
from datetime import datetime, timedelta, timezone

from ops.activity import AgentRun, _client, agent_enabled

logger = logging.getLogger(__name__)

AGENT = "image_cleanup"

_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


def _int_env(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, "").strip() or default))
    except ValueError:
        return default


def _parse_ts(value) -> "datetime | None":
    """ISO-8601 -> aware datetime, or None. Tolerates a trailing Z."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def partition(history, *, now: datetime, ttl_hours: int, keep: int):
    """
    Split a history array into (kept, expired). Pure — the part worth testing.

    An entry is KEPT iff it is among the newest `keep` AND younger than
    `ttl_hours`; everything else is expired (its R2 object and entry are
    removed). Ordering is by `created_at`; a missing/unparseable one sorts oldest
    and is treated as expired, so a malformed entry fails toward deletion rather
    than pinning an object alive forever. This is safe because the live image is
    never in history. `kept` is returned newest-last, matching how it is stored.
    """
    entries = [e for e in history if isinstance(e, dict)]
    cutoff = now - timedelta(hours=ttl_hours)

    def ts(e):
        return _parse_ts(e.get("created_at"))

    ordered = sorted(entries, key=lambda e: ts(e) or _EPOCH, reverse=True)
    kept, expired = [], []
    for i, e in enumerate(ordered):
        t = ts(e)
        if i < keep and t is not None and t >= cutoff:
            kept.append(e)
        else:
            expired.append(e)
    kept.sort(key=lambda e: ts(e) or _EPOCH)   # newest last, as stored
    return kept, expired


def _sweep(client, run_ctx, stats: dict, *, now, ttl_hours, keep, dry_run) -> None:
    rows = (client.table("incidents")
            .select("id, image_history")
            .neq("image_history", "[]")
            .limit(1000).execute()).data or []

    r2_client = None
    for row in rows:
        stats["incidents_scanned"] += 1
        history = row.get("image_history")
        if not isinstance(history, list) or not history:
            continue

        kept, expired = partition(history, now=now, ttl_hours=ttl_hours, keep=keep)
        if not expired:
            continue

        stats["entries_expired"] += len(expired)
        if dry_run:
            continue

        from art.generate_image import delete_from_r2, _default_r2_client
        if r2_client is None:
            r2_client = _default_r2_client()

        for e in expired:
            key = e.get("key")
            if not key:
                continue
            try:
                delete_from_r2(key, r2_client)
                stats["objects_deleted"] += 1
            except Exception as exc:              # noqa: BLE001
                # Keep the entry so the next pass retries the delete rather than
                # orphaning the object — better a late delete than a leak.
                stats["errors"] += 1
                kept.append(e)
                run_ctx.error_("r2_delete_failed", f"{key}: {exc}")

        try:
            client.table("incidents").update({"image_history": kept}).eq("id", row["id"]).execute()
        except Exception as exc:                   # noqa: BLE001
            stats["errors"] += 1
            run_ctx.error_("history_write_failed", f"{row.get('id')}: {exc}")


def run(supabase_client=None, trigger: str = "scheduler",
        dry_run: bool = False, now: "datetime | None" = None) -> dict:
    """Expire aged/overflow image-history versions. Never raises."""
    stats = {"incidents_scanned": 0, "entries_expired": 0,
             "objects_deleted": 0, "errors": 0, "dry_run": dry_run}

    if not agent_enabled(AGENT):
        logger.info("image_cleanup: disabled via AGENT_DISABLED — skipping")
        stats["skipped"] = True
        return stats

    ttl_hours = _int_env("IMAGE_HISTORY_TTL_HOURS", 8)
    keep = _int_env("IMAGE_HISTORY_KEEP", 3)
    now = now or datetime.now(timezone.utc)
    stats["ttl_hours"] = ttl_hours
    stats["keep"] = keep

    try:
        client = _client(supabase_client)
        with AgentRun(AGENT, trigger=trigger, client=client) as run_ctx:
            try:
                _sweep(client, run_ctx, stats, now=now,
                       ttl_hours=ttl_hours, keep=keep, dry_run=dry_run)
            except Exception as exc:               # noqa: BLE001
                stats["errors"] += 1
                run_ctx.error_("image_cleanup_failed", f"sweep failed: {exc}")
            for key, value in stats.items():
                run_ctx.stat(key, value)
    except Exception as exc:                        # noqa: BLE001
        logger.exception("image_cleanup: unhandled failure: %s", exc)
        stats["errors"] += 1

    return stats
