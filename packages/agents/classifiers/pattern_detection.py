"""
Pattern detection agent (spec v1.5 §5.3).

Detects three pattern types across published incidents:

  1. ENTITY    — named entity in 3+ separate incidents within 365 days.
                 Claude Haiku extracts entities from title+summary.

                 ⚠ `_entity_cache` buys nothing in production. Cloud Run runs
                 with min-instances=0 and this agent is called once a day from
                 ops/daily.py, so the "instance lifetime" the cache spans is a
                 single pass: every pass re-extracts from cold. The cache is
                 real only for repeated calls inside one process (local dev,
                 back-to-back manual triggers).

                 That makes PATTERN_MAX_EXTRACTIONS a hard coverage limit, not
                 the catch-up-over-time cap it reads as: the incident pool is
                 ordered newest-first, so the same newest N are re-extracted
                 daily and anything past N is never entity-checked at all. With
                 the pool under the cap this is only a cost question (~N Haiku
                 calls/day). Over the cap, entity detection is blind to the
                 tail, and run() says so in `entities_uncovered` and an
                 explicit log line rather than reporting a partial sweep as a
                 clean one. Raise the cap, or give extraction a persistent
                 store, before trusting entity coverage on a large archive.

  2. CRIME TYPE — same classification at severity ≥ 4 in 3+ incidents within
                  90 days, within the same area_name or block-number prefix.

  3. LOCATION  — 5+ incidents in the same area_name within 90 days (any class).

For each new pattern:
  - Deduplicates against pattern_alerts within the last 30 days.
  - Inserts a pattern_alerts row.
  - Inserts a war_room_queue notification (notification_type='pattern_alert').

Public API
----------
run(supabase_client=None) -> dict
    Returns: {alerts_created, patterns_found, entities_checked,
              entities_uncovered, errors}
"""

import json
import logging
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import anthropic
from dotenv import load_dotenv

load_dotenv(override=False)

logger = logging.getLogger(__name__)

# Training signal action values written by War Room routes when operator acts.
# Defined here as the canonical reference; TypeScript side mirrors these.
TRAINING_ACTION_CONFIRMED  = "pattern_confirmed"
TRAINING_ACTION_DISMISSED  = "pattern_dismissed"

# ── Thresholds ────────────────────────────────────────────────────────────────
ENTITY_WINDOW_DAYS      = 365
CRIMETYPE_WINDOW_DAYS   = 90
LOCATION_WINDOW_DAYS    = 90
ENTITY_THRESHOLD        = 3
CRIMETYPE_THRESHOLD     = 3
LOCATION_THRESHOLD      = 5
CRIMETYPE_MIN_SEVERITY  = 4
ALERT_DEDUP_DAYS        = 30

# Cap Haiku entity-extraction calls per run. Env-settable so the operator can
# raise it without a redeploy when the archive outgrows it — see the module
# docstring for why exceeding it is a coverage limit, not just a cost one.
try:
    _MAX_EXTRACTIONS_PER_RUN = int(os.getenv("PATTERN_MAX_EXTRACTIONS", "100"))
except ValueError:
    _MAX_EXTRACTIONS_PER_RUN = 100

# Module-level entity cache: incident_id → [entity strings].
# Lives for the lifetime of the PROCESS, which in production is one pass.
_entity_cache: dict[str, list[str]] = {}


# ── Anthropic client ──────────────────────────────────────────────────────────

def _get_anthropic_client() -> anthropic.Anthropic:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY must be set")
    return anthropic.Anthropic(api_key=api_key)


# ── Entity extraction ─────────────────────────────────────────────────────────

_ENTITY_SYSTEM = """\
Extract named proper nouns from a Yishun incident that could identify a RECURRING subject.

Target:
- Person names only when explicitly named (not "man", "woman", "resident", "victim")
- Named animals (only if given a specific name like "Tommy the cat")
- Specific organisations, businesses, or schools (not "a void deck stall" or "the coffee shop")
- Court case numbers or other formal identifiers

Return JSON only: {"entities": ["Name1", "Name2"]}
Return {"entities": []} if no qualifying entities are found.
"""


def _extract_entities(
    client: anthropic.Anthropic,
    incident_id: str,
    title: str,
    summary: str,
) -> list[str]:
    """Return named entities for an incident, using cache when available."""
    if incident_id in _entity_cache:
        return _entity_cache[incident_id]

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            temperature=0.0,
            system=_ENTITY_SYSTEM,
            messages=[{
                "role": "user",
                "content": f"Title: {title}\n\nSummary: {summary[:600]}",
            }],
        )
        raw = response.content[0].text.strip()
        raw = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`")
        data = json.loads(raw)
        entities: list[str] = [
            e.strip().lower() for e in (data.get("entities") or [])
            if isinstance(e, str) and e.strip()
        ]
    except Exception as exc:
        logger.debug("Entity extraction failed for %s: %s", incident_id, exc)
        entities = []

    _entity_cache[incident_id] = entities
    return entities


# ── Area helpers ──────────────────────────────────────────────────────────────

def _block_prefix(block: str | None) -> str | None:
    """Return first 3 digits of a block number, or None."""
    if not block:
        return None
    digits = re.sub(r"[^0-9]", "", block)
    return digits[:3] if len(digits) >= 3 else None


def _area_group_key(incident: dict) -> str | None:
    """Canonical area key for grouping: area_name takes precedence, then block prefix."""
    area = (incident.get("area_name") or "").strip()
    if area:
        return f"area:{area.lower()}"
    prefix = _block_prefix(incident.get("block_number"))
    if prefix:
        return f"block:{prefix}"
    return None


# ── Deduplication ─────────────────────────────────────────────────────────────

def _is_duplicate_alert(supabase, pattern_type: str, pattern_value: str) -> bool:
    """Return True if an alert for this pattern was created in the last 30 days."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=ALERT_DEDUP_DAYS)).isoformat()
    try:
        result = (
            supabase.table("pattern_alerts")
            .select("id", count="exact", head=True)
            .eq("pattern_type",  pattern_type)
            .eq("pattern_value", pattern_value)
            .gte("created_at",   cutoff)
            .execute()
        )
        return (result.count or 0) > 0
    except Exception as exc:
        logger.warning("Dedup check failed (%s/%s): %s — treating as non-duplicate", pattern_type, pattern_value, exc)
        return False


# ── Alert + notification creation ─────────────────────────────────────────────

def _fire_alert(
    supabase,
    pattern_type:    str,
    pattern_value:   str,
    incident_ids:    list[str],
    incident_titles: list[str],
    window_days:     int,
) -> bool:
    """
    Create a pattern_alerts row and a War Room queue notification.
    Returns True if a new alert was created, False if it was deduplicated.
    """
    if _is_duplicate_alert(supabase, pattern_type, pattern_value):
        logger.debug("Pattern DEDUP — %s/%s (alerted in last 30d)", pattern_type, pattern_value)
        return False

    # Insert pattern_alerts
    try:
        alert_res = (
            supabase.table("pattern_alerts")
            .insert({
                "pattern_type":  pattern_type,
                "pattern_value": pattern_value,
                "incident_ids":  incident_ids,
                "window_days":   window_days,
                "status":        "pending",
            })
            .select("id")
            .execute()
        )
        # supabase-py insert().select() returns a list, not a single object —
        # this builder has no .single() (mirrors the pattern in backfill_agent).
        if not alert_res.data:
            raise RuntimeError("pattern_alerts insert returned no row")
        alert_id: str = alert_res.data[0]["id"]
    except Exception as exc:
        logger.error("pattern_alerts insert failed (%s/%s): %s", pattern_type, pattern_value, exc)
        return False

    # Build summary
    titles_preview = "; ".join(f'"{t[:60]}"' for t in incident_titles[:4])
    if len(incident_titles) > 4:
        titles_preview += f" (+{len(incident_titles) - 4} more)"

    summary = (
        f"{len(incident_ids)} incidents in {window_days} days. "
        f"Pattern: {pattern_value}. "
        f"Incidents: {titles_preview}. "
        "Review for editorial linking or people profile."
    )

    notification = {
        "raw_content": {
            "notification_type": "pattern_alert",
            "pattern_alert_id":  alert_id,
            "pattern_type":      pattern_type,
            "pattern_value":     pattern_value,
            "incident_ids":      incident_ids,
            "incident_titles":   incident_titles,
            "window_days":       window_days,
        },
        "source_url":              "internal://pattern-detection",
        "source_type":             "msm",
        "proposed_title":          f"PATTERN ALERT — {pattern_type.upper()}: {pattern_value}"[:200],
        "proposed_summary":        summary,
        "proposed_classification": "dagger",
        "proposed_severity":       3,
        "agent_confidence":        0.85,
        "corroboration_count":     len(incident_ids),
        "edmw_signal_count":       0,
        "status":                  "pending",
    }

    try:
        supabase.table("war_room_queue").insert(notification).execute()
    except Exception as exc:
        logger.error("war_room_queue notification insert failed: %s", exc)
        return False

    logger.info(
        "Pattern ALERT created — %s/%s (%d incidents, %dd window)",
        pattern_type, pattern_value, len(incident_ids), window_days,
    )
    return True


# ── Pattern checks ────────────────────────────────────────────────────────────

def _check_entity_patterns(supabase, incidents: list[dict],
                           anthropic_client) -> tuple[int, int, int]:
    """
    Returns (alerts_created, entities_checked, uncovered).

    `uncovered` is how many incidents the extraction cap excluded. It is not a
    backlog that clears itself: the pool is newest-first and the cache is cold
    every pass, so the same incidents are excluded every day (module docstring).
    """
    entities_checked = 0
    alerts_created   = 0
    uncovered        = 0

    # entity_name → list of incident dicts
    entity_map: dict[str, list[dict]] = defaultdict(list)

    extractions_this_run = 0
    for inc in incidents:
        if extractions_this_run >= _MAX_EXTRACTIONS_PER_RUN and inc["id"] not in _entity_cache:
            uncovered += 1
            continue
        if inc["id"] not in _entity_cache:
            extractions_this_run += 1
        entities = _extract_entities(
            anthropic_client, inc["id"], inc["title"], inc.get("summary", "")
        )
        entities_checked += 1
        for entity in entities:
            entity_map[entity].append(inc)

    for entity, matching_incs in entity_map.items():
        if len(matching_incs) < ENTITY_THRESHOLD:
            continue
        ids    = [i["id"]    for i in matching_incs]
        titles = [i["title"] for i in matching_incs]
        if _fire_alert(supabase, "entity", entity, ids, titles, ENTITY_WINDOW_DAYS):
            alerts_created += 1

    return alerts_created, entities_checked, uncovered


def _check_crime_type_patterns(supabase, incidents: list[dict]) -> int:
    """Returns alerts_created."""
    alerts_created = 0

    # Key: (classification, severity, area_group_key) → [incidents]
    groups: dict[tuple, list[dict]] = defaultdict(list)

    for inc in incidents:
        sev = inc.get("severity") or 0
        if sev < CRIMETYPE_MIN_SEVERITY:
            continue
        if inc.get("classification") == "heart":
            continue
        area_key = _area_group_key(inc)
        if not area_key:
            continue
        key = (inc["classification"], sev, area_key)
        groups[key].append(inc)

    for (classification, severity, area_key), matching_incs in groups.items():
        if len(matching_incs) < CRIMETYPE_THRESHOLD:
            continue
        area_label  = area_key.split(":", 1)[1].replace("-", " ").title()
        pattern_val = f"{classification} severity {severity} in {area_label}"
        ids    = [i["id"]    for i in matching_incs]
        titles = [i["title"] for i in matching_incs]
        if _fire_alert(supabase, "crime_type", pattern_val, ids, titles, CRIMETYPE_WINDOW_DAYS):
            alerts_created += 1

    return alerts_created


def _check_location_patterns(supabase, incidents: list[dict]) -> int:
    """Returns alerts_created."""
    alerts_created = 0

    area_groups: dict[str, list[dict]] = defaultdict(list)
    for inc in incidents:
        area = (inc.get("area_name") or "").strip()
        if area:
            area_groups[area.lower()].append(inc)

    for area_lower, matching_incs in area_groups.items():
        if len(matching_incs) < LOCATION_THRESHOLD:
            continue
        area_label = area_lower.title()
        ids    = [i["id"]    for i in matching_incs]
        titles = [i["title"] for i in matching_incs]
        if _fire_alert(supabase, "location", area_label, ids, titles, LOCATION_WINDOW_DAYS):
            alerts_created += 1

    return alerts_created


# ── Public API ────────────────────────────────────────────────────────────────

def run(supabase_client=None) -> dict:
    """
    Run all three pattern checks against published incidents.

    Returns:
        {alerts_created, patterns_found, entities_checked, entities_uncovered,
         errors}

    A non-zero `entities_uncovered` means the entity sweep was partial and will
    stay partial on the same incidents every day — see the module docstring.
    """
    if supabase_client is None:
        from classifiers.corroboration import get_supabase_client
        try:
            supabase_client = get_supabase_client()
        except EnvironmentError as exc:
            logger.error("Pattern detection: Supabase not configured: %s", exc)
            return {"alerts_created": 0, "patterns_found": 0, "entities_checked": 0, "errors": 1}

    stats = {"alerts_created": 0, "patterns_found": 0, "entities_checked": 0,
             "entities_uncovered": 0, "errors": 0}

    now     = datetime.now(timezone.utc)
    cutoffs = {
        "entity":     (now - timedelta(days=ENTITY_WINDOW_DAYS)).isoformat(),
        "crime_type": (now - timedelta(days=CRIMETYPE_WINDOW_DAYS)).isoformat(),
        "location":   (now - timedelta(days=LOCATION_WINDOW_DAYS)).isoformat(),
    }

    # ── Fetch incident sets ───────────────────────────────────────────────────
    def _fetch(cutoff: str, extra_filters: dict | None = None) -> list[dict]:
        q = (
            supabase_client.table("incidents")
            .select("id,title,summary,classification,severity,area_name,block_number,published_at")
            .eq("is_published", True)
            .gte("published_at", cutoff)
            .order("published_at", desc=True)
        )
        if extra_filters:
            for col, val in extra_filters.items():
                q = q.eq(col, val)
        result = q.execute()
        return result.data or []

    try:
        entity_incidents   = _fetch(cutoffs["entity"])
        crimetype_incidents = _fetch(cutoffs["crime_type"])
        location_incidents  = _fetch(cutoffs["location"])
    except Exception as exc:
        logger.error("Pattern detection: incident fetch failed: %s", exc)
        return {**stats, "errors": 1}

    logger.info(
        "Pattern detection: entity_pool=%d crime_pool=%d location_pool=%d",
        len(entity_incidents), len(crimetype_incidents), len(location_incidents),
    )

    # ── 1. Entity patterns ────────────────────────────────────────────────────
    try:
        anthropic_client = _get_anthropic_client()
        entity_alerts, entities_checked, uncovered = _check_entity_patterns(
            supabase_client, entity_incidents, anthropic_client
        )
        stats["alerts_created"]    += entity_alerts
        stats["patterns_found"]    += entity_alerts
        stats["entities_checked"]   = entities_checked
        stats["entities_uncovered"] = uncovered
        if uncovered:
            # Never let a partial sweep read as a clean one: without this line,
            # "0 entity patterns found" is indistinguishable from "we only looked
            # at the newest 100 incidents and will only ever look at those".
            logger.warning(
                "Pattern detection: entity coverage INCOMPLETE — %d of %d incident(s) "
                "checked, %d never examined (PATTERN_MAX_EXTRACTIONS=%d). Entity "
                "patterns involving the excluded incidents cannot be found.",
                entities_checked, len(entity_incidents), uncovered,
                _MAX_EXTRACTIONS_PER_RUN,
            )
    except Exception as exc:
        logger.error("Entity pattern check failed: %s", exc)
        stats["errors"] += 1

    # ── 2. Crime type patterns ────────────────────────────────────────────────
    try:
        crime_alerts = _check_crime_type_patterns(supabase_client, crimetype_incidents)
        stats["alerts_created"] += crime_alerts
        stats["patterns_found"] += crime_alerts
    except Exception as exc:
        logger.error("Crime type pattern check failed: %s", exc)
        stats["errors"] += 1

    # ── 3. Location patterns ──────────────────────────────────────────────────
    try:
        loc_alerts = _check_location_patterns(supabase_client, location_incidents)
        stats["alerts_created"] += loc_alerts
        stats["patterns_found"] += loc_alerts
    except Exception as exc:
        logger.error("Location pattern check failed: %s", exc)
        stats["errors"] += 1

    logger.info(
        "Pattern detection complete — alerts_created=%d patterns_found=%d "
        "entities_checked=%d entities_uncovered=%d errors=%d",
        stats["alerts_created"], stats["patterns_found"],
        stats["entities_checked"], stats["entities_uncovered"], stats["errors"],
    )
    return stats
