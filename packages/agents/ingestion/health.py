"""
scraper_health writer — one row per source, per pass, written from the LIVE
ingestion path (TechSpec §8a).

WHY THIS MODULE EXISTS
----------------------
The table used to be written by `scrapers.log_scraper_run`, which was called
only from `scrapers.scrape_all` — and scrape_all lost its last caller when
ingestion moved to the `ingestion/sources/` adapters. Nothing wrote a row after
that, but `ops/supervisor.py` and both War Room health views kept READING the
table. The fleet was being graded on rows that had stopped moving.

A health table that only ever gets staler is worse than no health table: it
reports a green dot for a source that has not run in months, and it reports it
with the same confidence as a real result. So the writer now lives on the path
that actually runs.

THE KEY IS THE STABLE SOURCE ID, NOT THE DISPLAY NAME
-----------------------------------------------------
Rows are keyed by `source.name` — `stomp`, `straits_times` — the same id that
keys `pipeline_state`. The old writer used display names (`Stomp`, `The Straits
Times`), and the supervisor cross-references the two tables by this key: two
spellings of one source count it TWICE toward the ">= 3 sources anomalous"
threshold that decides whether the operator gets an email. One broken source
could therefore have mailed as if it were three.

Never raises. Health logging that can crash a pass turns an observability
outage into a data outage — the same rule `ops/` lives by, for the same reason.
"""

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# 0 items is the NORMAL case: the scrapers filter for Yishun and most sources
# have nothing on most days. Only an unbroken streak means the listing page
# changed shape underneath us. (ops/supervisor escalates its own, longer streak
# to an anomaly; this is the earlier, quieter War Room signal.)
ZERO_STREAK_WARNING = 3

# A run this much slower than the source's own 7-day baseline is worth a look —
# usually a site that started serving a bot-check page slowly rather than 403ing.
SLOW_RUN_FACTOR = 3

BASELINE_DAYS = 7


def classify(*, items_found, duration_ms, errors, last_consecutive_zeros=0,
             avg_duration_7d=None):
    """
    Grade one source run: returns (status, status_reason, consecutive_zeros).

    Pure — no I/O, no clock. This is the part worth testing.

    Status rules (TechSpec §8a):
      error   — the fetch failed (`errors` non-empty)
      warning — 0 items for ZERO_STREAK_WARNING+ consecutive runs, or a run
                SLOW_RUN_FACTOR x slower than this source's 7-day average
      ok      — everything else
    """
    consecutive_zeros = 0 if items_found > 0 else int(last_consecutive_zeros or 0) + 1

    if errors:
        return "error", "; ".join(str(e) for e in errors[:3])[:500], consecutive_zeros

    if consecutive_zeros >= ZERO_STREAK_WARNING:
        return ("warning",
                f"0 items for {consecutive_zeros} consecutive runs",
                consecutive_zeros)

    if avg_duration_7d and duration_ms and duration_ms > SLOW_RUN_FACTOR * avg_duration_7d:
        return ("warning",
                f"duration {duration_ms}ms is >{SLOW_RUN_FACTOR}x the 7d avg "
                f"({avg_duration_7d}ms)",
                consecutive_zeros)

    return "ok", None, consecutive_zeros


def _last_consecutive_zeros(client, source_name: str) -> int:
    """Zero-streak carried by this source's newest row. 0 on any failure."""
    try:
        res = (client.table("scraper_health")
               .select("consecutive_zeros")
               .eq("source_name", source_name)
               .order("scraped_at", desc=True)
               .limit(1).execute())
        rows = res.data or []
        return int(rows[0].get("consecutive_zeros") or 0) if rows else 0
    except Exception as exc:                          # noqa: BLE001 — see module docstring
        logger.debug("health: zero-streak read failed for %s: %s", source_name, exc)
        return 0


def _avg_duration(client, source_name: str) -> int | None:
    """This source's mean run duration over BASELINE_DAYS. None on any failure."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=BASELINE_DAYS)).isoformat()
    try:
        res = (client.table("scraper_health")
               .select("duration_ms")
               .eq("source_name", source_name)
               .gte("scraped_at", cutoff).execute())
        durations = [r["duration_ms"] for r in (res.data or [])
                     if r.get("duration_ms") is not None]
    except Exception as exc:                          # noqa: BLE001
        logger.debug("health: baseline read failed for %s: %s", source_name, exc)
        return None
    return int(sum(durations) / len(durations)) if durations else None


def record(source_name: str, source_type: str, *, items_found: int,
           items_passed_s1: int = 0, duration_ms: int | None = None,
           errors: list[str] | None = None, client=None) -> dict | None:
    """
    Write one scraper_health row for a source the pass actually fetched.

    Returns the row written, or None if nothing was written. NEVER raises.

    Only call this for sources that were really attempted: a row for a source
    the pass skipped (budget exhausted, deadline hit before its turn) would read
    as a genuine zero-item run and walk that source toward a false zero-streak
    warning.
    """
    if client is None:
        logger.debug("health: no Supabase client — skipping health row for %s", source_name)
        return None

    try:
        avg = _avg_duration(client, source_name)
        status, status_reason, consecutive_zeros = classify(
            items_found=items_found,
            duration_ms=duration_ms,
            errors=errors,
            last_consecutive_zeros=_last_consecutive_zeros(client, source_name),
            avg_duration_7d=avg,
        )
        row = {
            "source_name":       source_name,
            "source_type":       source_type,
            "items_found":       items_found,
            "items_passed_s1":   items_passed_s1,
            "errors":            errors or None,
            "duration_ms":       duration_ms,
            "status":            status,
            "status_reason":     status_reason,
            "consecutive_zeros": consecutive_zeros,
            "avg_duration_7d":   avg,
        }
        client.table("scraper_health").insert(row).execute()
        logger.debug("health [%s]: status=%s items=%d passed_s1=%d duration=%sms",
                     source_name, status, items_found, items_passed_s1, duration_ms)
        return row
    except Exception as exc:                          # noqa: BLE001
        logger.warning("health: write failed for %s (non-fatal): %s", source_name, exc)
        return None
