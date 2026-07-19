"""
Per-day Stage 1 RPD budget persistence (INGESTION_DESIGN.md §10, review S2).

filters/stage1_quota.py::Stage1DailyQuota is per-instance, in-memory, with no
cross-invocation persistence — a true requests-per-day limit only holds if the
quota is seeded from a persisted daily counter. This module wraps it to
seed-from and persist-to a counter keyed by SGT calendar date, so a daily
ingestion run plus any manual same-day re-run share one RPD ceiling instead of
each starting from zero.

Note the two calendars: the counter rolls over at SGT midnight (operator-local),
while Gemini's own RPD quota resets at midnight US/Pacific. Ours is the more
conservative of the two for most of the day, and a real RPD 429 halts the pass
regardless — see Stage1DailyQuota.mark_rpd_exhausted().

N2: this is a SEPARATE file from the deprecated run_backfill()'s
stage1_session_usage.json — since that path is now guarded off (§10b step 3),
no concurrent-write collision can occur between the two.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from filters.stage1_quota import RPD_HARD_LIMIT, Stage1DailyQuota

logger = logging.getLogger(__name__)

SGT = timezone(timedelta(hours=8))
USAGE_PATH = Path(__file__).parent / "stage1_daily_usage.json"


def _today_sgt() -> str:
    return datetime.now(SGT).date().isoformat()


def load_daily_budget(path: Path = USAGE_PATH) -> Stage1DailyQuota:
    """
    Return a Stage1DailyQuota seeded with today's (SGT) persisted usage.

    If no usage has been recorded yet for today's SGT date (new day, or
    first run ever), returns a fresh zeroed quota.
    """
    budget = Stage1DailyQuota()

    if not path.exists():
        return budget

    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("budget: failed to read %s — starting fresh: %s", path, exc)
        return budget

    if payload.get("date") != _today_sgt():
        return budget  # stale — new SGT day, start fresh

    budget.tokens_used = payload.get("tokens_used", 0)
    budget.calls_made = payload.get("calls_made", 0)
    budget.halted = budget.calls_made >= RPD_HARD_LIMIT

    if budget.halted:
        logger.warning(
            "budget: seeded already-halted (%d requests used today, SGT)",
            budget.calls_made,
        )

    return budget


def save_daily_budget(budget: Stage1DailyQuota, path: Path = USAGE_PATH) -> None:
    """
    Persist today's (SGT) cumulative usage so the next invocation (scheduled
    run or manual same-day re-run) seeds from it via load_daily_budget().
    """
    payload = {
        "date": _today_sgt(),
        "tokens_used": budget.tokens_used,
        "calls_made": budget.calls_made,
        "halted": budget.halted,
    }
    path.write_text(json.dumps(payload, indent=2))
    logger.debug("budget: persisted %s", payload)
