"""
Recalibration agent (spec v1.5 §5.5).

Monitors training_signals for systematic operator corrections and writes a
calibration_log.json summary when any signal type accumulates ≥ 20 corrections.

The Stage 2 writer reads calibration_log.json at call time and injects the
top-3 common mistakes as negative examples in the system prompt.

NOTE: calibration_log.json is a local file. On Cloud Run (ephemeral filesystem)
it is lost on restart. For production persistence, move to Supabase or GCS.
The file path is intentionally in the classifiers directory alongside this module.

Public API
----------
check(supabase_client=None) -> dict
    Returns: {recalibrated: bool, signal_types_updated: list[str], errors: int}
"""

import json
import logging
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from dotenv import load_dotenv

load_dotenv(override=False)

logger = logging.getLogger(__name__)

CORRECTION_THRESHOLD = 20
CALIBRATION_LOG_PATH = Path(__file__).parent / "calibration_log.json"
MODEL = "claude-haiku-4-5-20251001"


# ── Correction analysis ───────────────────────────────────────────────────────

def _fetch_corrections(supabase_client) -> dict:
    """
    Query training_signals for all operator corrections.
    Returns dict keyed by signal_type containing (from, to) Counter objects.
    """
    result = (
        supabase_client.table("training_signals")
        .select(
            "action,original_classification,edited_classification,"
            "original_severity,edited_severity,"
            "agent_role_proposed,operator_role_confirmed,"
            "operator_changes"
        )
        .execute()
    )
    rows = result.data or []

    classification_corrections: Counter = Counter()
    severity_corrections:       Counter = Counter()
    role_corrections:           Counter = Counter()

    for row in rows:
        # Classification edits
        orig_cls  = row.get("original_classification")
        edit_cls  = row.get("edited_classification")
        if orig_cls and edit_cls and orig_cls != edit_cls:
            classification_corrections[(orig_cls, edit_cls)] += 1

        # Severity edits
        orig_sev  = row.get("original_severity")
        edit_sev  = row.get("edited_severity")
        if orig_sev is not None and edit_sev is not None and orig_sev != edit_sev:
            severity_corrections[(orig_sev, edit_sev)] += 1

        # Role corrections
        proposed  = row.get("agent_role_proposed")
        confirmed = row.get("operator_role_confirmed")
        if proposed and confirmed and proposed != confirmed:
            role_corrections[(proposed, confirmed)] += 1

    return {
        "classification": classification_corrections,
        "severity":       severity_corrections,
        "role":           role_corrections,
    }


def _total_corrections(counter: Counter) -> int:
    return sum(counter.values())


# ── Common-mistakes generation ────────────────────────────────────────────────

def _generate_mistakes(
    client: anthropic.Anthropic,
    signal_type: str,
    corrections: Counter,
) -> list[str]:
    """Ask Haiku to convert (from, to) correction counts into plain-English mistakes."""
    top = corrections.most_common(10)
    lines = "\n".join(f"  {count}× {orig!r} → {edited!r}" for (orig, edited), count in top)

    response = client.messages.create(
        model=MODEL,
        max_tokens=300,
        temperature=0.0,
        system=(
            "You write concise quality guidelines for an AI editorial agent. "
            "Given correction statistics, produce 3 plain-English rules to reduce mistakes. "
            "Return JSON only: {\"mistakes\": [\"Rule 1\", \"Rule 2\", \"Rule 3\"]}"
        ),
        messages=[{
            "role":    "user",
            "content": (
                f"Signal type: {signal_type}\n"
                f"Top operator corrections (agent output → operator correction, count):\n"
                f"{lines}\n\n"
                "Write 3 rules to help the agent avoid these mistakes."
            ),
        }],
    )

    raw = response.content[0].text.strip()
    import re
    raw = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`")
    data = json.loads(raw)
    return [str(m) for m in (data.get("mistakes") or [])[:3]]


# ── Calibration log I/O ───────────────────────────────────────────────────────

def _read_log() -> list[dict]:
    if CALIBRATION_LOG_PATH.exists():
        try:
            return json.loads(CALIBRATION_LOG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def _write_log(entries: list[dict]) -> None:
    CALIBRATION_LOG_PATH.write_text(
        json.dumps(entries, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


# ── Public API ────────────────────────────────────────────────────────────────

def check(supabase_client=None) -> dict:
    """
    Check training_signals for correction patterns and write calibration_log.json
    if any signal type reaches the correction threshold.

    Returns:
        {recalibrated: bool, signal_types_updated: list[str], errors: int}
    """
    if supabase_client is None:
        from classifiers.corroboration import get_supabase_client
        try:
            supabase_client = get_supabase_client()
        except EnvironmentError as exc:
            logger.error("Recalibration: Supabase not configured: %s", exc)
            return {"recalibrated": False, "signal_types_updated": [], "errors": 1}

    stats: dict = {"recalibrated": False, "signal_types_updated": [], "errors": 0}

    try:
        all_corrections = _fetch_corrections(supabase_client)
    except Exception as exc:
        logger.error("Recalibration: failed to fetch training signals: %s", exc)
        return {**stats, "errors": 1}

    # Find signal types that have reached the threshold
    overdue: dict[str, Counter] = {
        sig_type: counter
        for sig_type, counter in all_corrections.items()
        if _total_corrections(counter) >= CORRECTION_THRESHOLD
    }

    if not overdue:
        logger.debug(
            "Recalibration: no signal type reached threshold (%d). "
            "Counts: %s",
            CORRECTION_THRESHOLD,
            {k: _total_corrections(v) for k, v in all_corrections.items()},
        )
        return stats

    try:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise EnvironmentError("ANTHROPIC_API_KEY must be set")
        client = anthropic.Anthropic(api_key=api_key)
    except Exception as exc:
        logger.error("Recalibration: Anthropic client error: %s", exc)
        return {**stats, "errors": 1}

    existing = {e["signal_type"]: e for e in _read_log()}

    for signal_type, counter in overdue.items():
        try:
            mistakes = _generate_mistakes(client, signal_type, counter)
            entry = {
                "signal_type":      signal_type,
                "correction_count": _total_corrections(counter),
                "common_mistakes":  mistakes,
                "last_updated":     datetime.now(timezone.utc).isoformat(),
            }
            existing[signal_type] = entry
            stats["signal_types_updated"].append(signal_type)
            logger.info(
                "Recalibration: updated '%s' — %d corrections, %d mistakes generated",
                signal_type, entry["correction_count"], len(mistakes),
            )
        except Exception as exc:
            logger.error("Recalibration: error for signal type '%s': %s", signal_type, exc)
            stats["errors"] += 1

    if stats["signal_types_updated"]:
        _write_log(list(existing.values()))
        stats["recalibrated"] = True

    return stats


def read_hints() -> list[str]:
    """
    Return the top-3 common mistakes across all signal types for Stage 2 injection.
    Returns an empty list if no log exists or the log is empty.
    """
    entries = _read_log()
    hints: list[str] = []
    for entry in entries:
        hints.extend(entry.get("common_mistakes", []))
    return hints[:3]
