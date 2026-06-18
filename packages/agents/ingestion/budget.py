"""
Per-day Groq TPD budget persistence (INGESTION_DESIGN.md §10, review S2).

scrapers/groq_budget.py::GroqBudget is per-instance, in-memory, with no
cross-invocation persistence — a true tokens-per-day limit only holds if the
budget is seeded from a persisted daily counter. This module wraps GroqBudget
to seed-from and persist-to a counter keyed by SGT calendar date, so a daily
ingestion run plus any manual same-day re-run share one 500k TPD ceiling
instead of each starting from zero.

N2: this is a SEPARATE file from the deprecated run_backfill()'s
groq_session_usage.json — since that path is now guarded off (§10b step 3),
no concurrent-write collision can occur between the two.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scrapers.groq_budget import GroqBudget, HARD_LIMIT

logger = logging.getLogger(__name__)

SGT = timezone(timedelta(hours=8))
USAGE_PATH = Path(__file__).parent / "groq_daily_usage.json"


def _today_sgt() -> str:
    return datetime.now(SGT).date().isoformat()


def load_daily_budget(path: Path = USAGE_PATH) -> GroqBudget:
    """
    Return a GroqBudget seeded with today's (SGT) persisted usage.

    If no usage has been recorded yet for today's SGT date (new day, or
    first run ever), returns a fresh zeroed GroqBudget.
    """
    budget = GroqBudget()

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
    budget.halted = budget.tokens_used >= HARD_LIMIT

    if budget.halted:
        logger.warning(
            "budget: seeded already-halted (%d tokens used today, SGT)",
            budget.tokens_used,
        )

    return budget


def save_daily_budget(budget: GroqBudget, path: Path = USAGE_PATH) -> None:
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
