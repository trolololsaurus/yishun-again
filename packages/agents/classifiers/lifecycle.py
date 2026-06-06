"""
Lifecycle agent — developing story timeout (spec v1.5 §5.2).

Runs weekly (Monday 00:00 SGT via APScheduler in main.py).

Logic:
  1. Fetch all incidents where is_developing = TRUE.
  2. For each: determine the date of the most recent source_timeline entry.
     If source_timeline is empty, fall back to published_at.
  3. If that date is older than TIMEOUT_DAYS (180), conclude the incident:
     - is_developing   = FALSE
     - latest_source_role = 'timeout'
     - concluded_at    = NOW()
     - conclusion_type = 'timeout'
  4. Insert a War Room queue notification so the operator can review the
     auto-conclusion and either confirm it or reopen the story.
     The notification is identified by raw_content.notification_type =
     'lifecycle_concluded' so QueueList routes it to LifecycleCard.

Public API
----------
run(supabase_client=None) -> dict
    Returns: {concluded: int, errors: int}
"""

import logging
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv(override=False)

logger = logging.getLogger(__name__)

TIMEOUT_DAYS = 180


# ── Helpers ──────────────────────────────────────────────────────────────────

def _latest_activity_date(incident: dict) -> datetime:
    """
    Return the most recent date of activity for a developing incident.
    Checks source_timeline entries first; falls back to published_at.
    """
    timeline = incident.get("source_timeline") or []
    dates: list[datetime] = []

    for entry in timeline:
        raw = entry.get("date", "")
        if not raw:
            continue
        try:
            # Entries store ISO date strings (YYYY-MM-DD or full ISO)
            if "T" in raw:
                dt = datetime.fromisoformat(raw)
            else:
                dt = datetime.fromisoformat(raw + "T00:00:00")
            dates.append(dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt)
        except ValueError:
            pass

    if dates:
        return max(dates)

    # No timeline entries — use published_at
    pub = incident.get("published_at") or ""
    try:
        dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
        return dt
    except (ValueError, AttributeError):
        return datetime.now(timezone.utc) - timedelta(days=TIMEOUT_DAYS + 1)


def _build_notification_row(incident: dict) -> dict:
    """Build a war_room_queue notification row for an auto-concluded incident."""
    source_urls: list[str] = incident.get("source_urls") or []
    first_url = source_urls[0] if source_urls else ""

    return {
        "raw_content": {
            "notification_type": "lifecycle_concluded",
            "incident_id":       incident["id"],
            "incident_title":    incident["title"],
            "incident_slug":     incident.get("slug", ""),
            "concluded_reason":  "No new sources in 180 days.",
        },
        "source_url":              first_url or "internal://lifecycle-timeout",
        "source_type":             "msm",
        "proposed_title":          f"AUTO-CONCLUDED: {incident['title']}"[:200],
        "proposed_summary":        (
            "No new sources in 180 days. Auto-concluded. "
            "Review if incorrect — click REOPEN to restore as developing story."
        ),
        "proposed_classification": incident.get("classification", "dagger"),
        "proposed_severity":       incident.get("severity", 1),
        "agent_confidence":        1.0,
        "corroboration_count":     0,
        "edmw_signal_count":       0,
        "status":                  "pending",
    }


# ── Public API ────────────────────────────────────────────────────────────────

def run(supabase_client=None) -> dict:
    """
    Run the lifecycle timeout check.

    Args:
        supabase_client: Pre-built admin Supabase client. If None, one is created.

    Returns:
        {"concluded": int, "errors": int}
    """
    if supabase_client is None:
        from classifiers.corroboration import get_supabase_client
        try:
            supabase_client = get_supabase_client()
        except EnvironmentError as exc:
            logger.error("Lifecycle: Supabase not configured: %s", exc)
            return {"concluded": 0, "errors": 1}

    cutoff = datetime.now(timezone.utc) - timedelta(days=TIMEOUT_DAYS)
    stats  = {"concluded": 0, "errors": 0}

    # Fetch all currently-developing incidents
    try:
        result = (
            supabase_client.table("incidents")
            .select("id,title,slug,classification,severity,source_urls,"
                    "source_timeline,published_at,latest_source_role")
            .eq("is_developing", True)
            .eq("is_published", True)
            .execute()
        )
        developing = result.data or []
    except Exception as exc:
        logger.error("Lifecycle: failed to fetch developing incidents: %s", exc)
        return {"concluded": 0, "errors": 1}

    logger.info("Lifecycle: checking %d developing incident(s)", len(developing))

    for incident in developing:
        try:
            last_activity = _latest_activity_date(incident)

            if last_activity >= cutoff:
                logger.debug(
                    "Lifecycle: ACTIVE '%s' — last activity %s",
                    incident["title"][:60], last_activity.date(),
                )
                continue

            logger.info(
                "Lifecycle: TIMEOUT '%s' — last activity %s (>180 days ago)",
                incident["title"][:60], last_activity.date(),
            )

            # Conclude the incident
            supabase_client.table("incidents").update({
                "is_developing":      False,
                "latest_source_role": "timeout",
                "concluded_at":       datetime.now(timezone.utc).isoformat(),
                "conclusion_type":    "timeout",
            }).eq("id", incident["id"]).execute()

            # Queue a War Room notification for operator review
            notification = _build_notification_row(incident)
            supabase_client.table("war_room_queue").insert(notification).execute()

            stats["concluded"] += 1

        except Exception as exc:
            logger.error(
                "Lifecycle: error processing incident %s: %s",
                incident.get("id", "?"), exc,
            )
            stats["errors"] += 1

    logger.info(
        "Lifecycle run complete — concluded=%d errors=%d",
        stats["concluded"], stats["errors"],
    )
    return stats
