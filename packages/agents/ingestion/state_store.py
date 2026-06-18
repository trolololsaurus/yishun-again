"""
pipeline_state / pipeline_run_history read-write (INGESTION_DESIGN.md §8,
migration 006).

Uses the existing Supabase admin client (classifiers.corroboration
.get_supabase_client). No business logic — the orchestrator decides WHAT
watermark/status to write; this module only persists it.
"""

import logging
from dataclasses import asdict
from datetime import date, datetime, timezone

from classifiers.corroboration import get_supabase_client
from ingestion.contracts import IngestionReport

logger = logging.getLogger(__name__)


def get(source_name: str, client=None) -> date | None:
    """
    Return the persisted watermark (max published_at successfully ingested)
    for `source_name`, or None if the source has never completed a run
    (first run / cold start, §5.3).
    """
    if client is None:
        client = get_supabase_client()

    result = (
        client.table("pipeline_state")
        .select("watermark")
        .eq("source_name", source_name)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    if not rows:
        return None

    watermark = rows[0].get("watermark")
    if not watermark:
        return None
    if isinstance(watermark, date):
        return watermark
    return date.fromisoformat(watermark)


def update(
    source_name: str,
    watermark: date | None,
    status: str,
    reason: str | None = None,
    client=None,
) -> None:
    """
    Upsert pipeline_state for `source_name`.

    Watermark write rule (§8): `watermark` MUST be the max `published_at`
    actually ingested this run — NEVER `now()` (using "now" would skip items
    published-but-not-yet-indexed). For a source that was SKIPPED by the
    FallbackLadder (§6), the caller must pass the source's PREVIOUS
    (unchanged) watermark — a blocked source's window is retried next run,
    never advanced.

    `status` is one of pipeline_state.last_status's CHECK values:
    'ok' | 'degraded' | 'blocked' | 'unavailable'. `last_run_at` (when the
    source last completed SUCCESSFULLY) is only updated when status == 'ok'.

    `consecutive_failures` is read, then reset to 0 on 'ok' or incremented
    otherwise — v1 only records this (§8); no auto-disable.
    """
    if client is None:
        client = get_supabase_client()

    existing = (
        client.table("pipeline_state")
        .select("consecutive_failures")
        .eq("source_name", source_name)
        .limit(1)
        .execute()
    )
    prev_failures = (existing.data or [{}])[0].get("consecutive_failures", 0) if existing.data else 0
    consecutive_failures = 0 if status == "ok" else prev_failures + 1

    now_iso = datetime.now(timezone.utc).isoformat()
    row = {
        "source_name":          source_name,
        "watermark":            watermark.isoformat() if watermark else None,
        "last_status":          status,
        "last_reason":          reason,
        "consecutive_failures": consecutive_failures,
        "updated_at":           now_iso,
    }
    if status == "ok":
        row["last_run_at"] = now_iso

    client.table("pipeline_state").upsert(row, on_conflict="source_name").execute()
    logger.debug(
        "pipeline_state updated: source=%s status=%s watermark=%s consecutive_failures=%d",
        source_name, status, row["watermark"], consecutive_failures,
    )


def record_run(report: IngestionReport, client=None) -> None:
    """
    Insert one row into pipeline_run_history (append-only, §8) — the full
    IngestionReport as JSONB, for observability.
    """
    if client is None:
        client = get_supabase_client()

    payload = asdict(report)
    payload["started_at"] = report.started_at.isoformat()
    payload["finished_at"] = report.finished_at.isoformat()

    client.table("pipeline_run_history").insert({
        "ran_at":       report.finished_at.isoformat(),
        "dry_run":      report.dry_run,
        "degraded":     report.degraded,
        "total_queued": report.total_queued,
        "report":       payload,
    }).execute()
    logger.debug(
        "pipeline_run_history recorded: degraded=%s total_queued=%d",
        report.degraded, report.total_queued,
    )
