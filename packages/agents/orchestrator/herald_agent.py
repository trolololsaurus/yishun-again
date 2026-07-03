"""
Herald agent — milestone detection and queuing (spec §8c).

Called from orchestrator/orchestrator.py's queue_insert/herald_check nodes (and
backfill_agent.py's auto-publish path) after every successful insert.
Checks all milestone thresholds against published incidents.

Milestone posts  → queued in war_room_queue for operator review.
Streak-broken    → badge annotated on the triggering queue item (no separate post).

All checks fail silently — a herald error must never disrupt the pipeline.
"""

import logging
import re
from datetime import date, datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# Death-free streak thresholds (days) that generate standalone milestone posts
_STREAK_MILESTONES = {30, 50, 100, 200, 300}

# Incident count milestones
_COUNT_MILESTONES = {100, 500, 1000}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _today_sgt() -> date:
    """Current date in Singapore Time (UTC+8)."""
    return (datetime.now(timezone.utc) + timedelta(hours=8)).date()


def _slugify(text: str) -> str:
    text = re.sub(r"[^a-z0-9\s-]", "", text.lower()).strip()
    return re.sub(r"[\s-]+", "-", text)[:70]


def _already_logged(
    client,
    milestone_type: str,
    value: Optional[int] = None,
    year: Optional[int] = None,
) -> bool:
    """Return True if this milestone type+value has already been logged (deduplication guard)."""
    q = client.table("milestones").select("id").eq("type", milestone_type)
    if value is not None:
        q = q.eq("value", value)
    if year is not None:
        q = q.gte("triggered_date", f"{year}-01-01").lt("triggered_date", f"{year + 1}-01-01")
    result = q.limit(1).execute()
    return bool(result.data)


def _log_milestone(
    client,
    milestone_type: str,
    value: int,
    source_url: str,
    triggered_date: str,
) -> None:
    """Insert a tracking row into the milestones table."""
    try:
        client.table("milestones").insert({
            "type":           milestone_type,
            "value":          value,
            "triggered_date": triggered_date,
            "source_url":     source_url,
        }).execute()
    except Exception as exc:
        logger.warning("Herald: milestones table insert failed (%s): %s", milestone_type, exc)


def _queue_milestone_post(
    client,
    title: str,
    summary: str,
    classification: str,
    severity: int,
    milestone_type: str,
    milestone_value: int,
    milestone_label: str,
    source_url: str,
    triggered_by_title: str,
    today_str: str,
) -> None:
    """Insert a milestone post into war_room_queue for operator one-tap review."""
    raw_content = {
        "is_milestone":       True,
        "milestone_type":     milestone_type,
        "milestone_value":    milestone_value,
        "milestone_label":    milestone_label,
        "triggered_by_url":   source_url,
        "triggered_by_title": triggered_by_title,
        "triggered_date":     today_str,
        # Fields the approve route reads to build the incidents row
        "source_urls":        [source_url],
        "hype_meter":         0,
        "chaos_contribution": 0.0,
        "tags":               ["milestone", milestone_type.replace("_", "-")],
    }
    try:
        client.table("war_room_queue").insert({
            "raw_content":             raw_content,
            "source_url":              source_url,
            "source_type":             "msm",
            "proposed_title":          title[:120],
            "proposed_summary":        summary,
            "proposed_classification": classification,
            "proposed_severity":       severity,
            "proposed_slug":           _slugify(title),
            "agent_confidence":        1.0,
            "corroboration_count":     1,
            "edmw_signal_count":       0,
            "status":                  "pending",
        }).execute()
        logger.info("Herald: queued milestone post — %s", title[:60])
    except Exception as exc:
        logger.warning("Herald: milestone queue insert failed (%s): %s", milestone_type, exc)


# ── Streak content templates ──────────────────────────────────────────────────

def _streak_title(streak_days: int) -> str:
    if streak_days >= 300:
        return f"Hell has frozen over. Day {streak_days} of Yishun being suspiciously quiet."
    if streak_days == 100:
        return f"Yishun hits {streak_days} days without a fatality. We're as shocked as you are."
    return f"Yishun hits {streak_days} days without a fatality."


def _streak_summary(streak_days: int, today_str: str, source_url: str) -> str:
    if streak_days >= 300:
        return (
            f"Yishun has now gone {streak_days} consecutive days without a confirmed fatality. "
            f"The streak, which began after the last published death incident, continues to confound "
            f"everyone paying attention. Residents are advised not to grow accustomed to this. "
            f"As of {today_str}. Source: {source_url}."
        )
    if streak_days == 100:
        return (
            f"Yishun hits 100 days without a confirmed fatality — a milestone that surprises us as much as anyone. "
            f"The streak is counted from the last published incident in which a death was explicitly confirmed "
            f"in source text. We're as shocked as you are. "
            f"As of {today_str}. Source: {source_url}."
        )
    return (
        f"Yishun has reached {streak_days} consecutive days without a confirmed fatality as of {today_str}. "
        f"The streak is counted from the last published incident where a death was explicitly confirmed "
        f"in source text. The archive continues to monitor. Source: {source_url}."
    )


# ── Individual milestone checks ───────────────────────────────────────────────

def _check_death_streak(
    client,
    deaths: Optional[int],
    queue_id: str,
    source_url: str,
    incident_title: str,
    today_str: str,
    triggered: list,
) -> None:
    """
    Check death-free streak milestones and streak-broken events.

    If draft has deaths >= 1 → annotate the queue item with streak_broken badge.
    Otherwise → check if the current streak has crossed a positive threshold.
    """
    today = date.fromisoformat(today_str)

    # Most recent published incident with a confirmed death
    last_death_res = (
        client.table("incidents")
        .select("published_at")
        .gte("deaths", 1)
        .eq("is_published", True)
        .order("published_at", desc=True)
        .limit(1)
        .execute()
    )

    last_death_pub = (
        last_death_res.data[0].get("published_at") if last_death_res.data else None
    )
    if last_death_pub:
        last_death_date = date.fromisoformat(last_death_pub[:10])
        current_streak = (today - last_death_date).days
    else:
        # No confirmed deaths on record — streak from first published incident
        first_res = (
            client.table("incidents")
            .select("published_at")
            .eq("is_published", True)
            .order("published_at")   # ascending — oldest first
            .limit(1)
            .execute()
        )
        first_pub = first_res.data[0].get("published_at") if first_res.data else None
        current_streak = (today - date.fromisoformat(first_pub[:10])).days if first_pub else 0

    logger.debug("Herald: death-free streak = %d days (deaths_in_draft=%r)", current_streak, deaths)

    if deaths is None or deaths < 1:
        # ── Positive death-free streak milestones ─────────────────────────────
        for threshold in sorted(_STREAK_MILESTONES):
            if current_streak >= threshold:
                if not _already_logged(client, "streak_milestone", value=threshold):
                    _queue_milestone_post(
                        client,
                        title=_streak_title(threshold),
                        summary=_streak_summary(threshold, today_str, source_url),
                        classification="heart",
                        severity=1,
                        milestone_type="streak_milestone",
                        milestone_value=threshold,
                        milestone_label=f"STREAK — {threshold} days",
                        source_url=source_url,
                        triggered_by_title=incident_title,
                        today_str=today_str,
                    )
                    _log_milestone(client, "streak_milestone", threshold, source_url, today_str)
                    triggered.append(f"streak_milestone ({threshold} days)")
                    logger.info("Herald: streak_milestone queued — %d days", threshold)


def _check_chaos_record(
    client,
    draft: dict,
    source_url: str,
    incident_title: str,
    today_str: str,
    triggered: list,
) -> None:
    """Check whether the current chaos index is an all-time high."""
    try:
        snapshots = (
            client.table("chaos_index_snapshots")
            .select("score_alltime, snapshot_at")
            .order("snapshot_at", desc=True)
            .limit(100)
            .execute()
        )
        rows = snapshots.data or []
        if len(rows) < 2:
            return  # Need at least 2 snapshots to establish a record

        latest_score = float(rows[0]["score_alltime"] or 0)
        prev_max     = max(float(r["score_alltime"] or 0) for r in rows[1:])

        if latest_score <= prev_max:
            return

        score_int = int(latest_score)
        if _already_logged(client, "chaos_record", value=score_int):
            return

        title = f"Yishun Chaos Index reaches {score_int} — highest ever recorded."
        summary = (
            f"The Yishun Again Chaos Index has hit {score_int}, a new all-time high since tracking began. "
            f"The index weights incident severity across classifications: Dagger ×3.0, Clown ×1.5, Heart −1.0. "
            f"New record. The bar was already low. "
            f"Triggered by: {incident_title}. Source: {source_url}."
        )
        _queue_milestone_post(
            client, title, summary, "dagger", 3,
            "chaos_record", score_int, f"CHAOS RECORD — {score_int}",
            source_url, incident_title, today_str,
        )
        _log_milestone(client, "chaos_record", score_int, source_url, today_str)
        triggered.append(f"chaos_record (score={score_int})")
    except Exception as exc:
        logger.warning("Herald: chaos record check failed: %s", exc)


def _check_incident_count(
    client,
    source_url: str,
    incident_title: str,
    today_str: str,
    triggered: list,
) -> None:
    """Check 100th / 500th / 1000th published incident milestones."""
    try:
        res = (
            client.table("incidents")
            .select("id", count="exact")
            .eq("is_published", True)
            .execute()
        )
        # +1: this queued item will be the next incident when approved
        projected = (res.count or 0) + 1

        for milestone in _COUNT_MILESTONES:
            if projected == milestone:
                if not _already_logged(client, "incident_count", value=milestone):
                    title = f"Yishun Again logs its {milestone}th incident. Still going."
                    summary = (
                        f"The Yishun Again archive has reached {milestone} logged incidents — "
                        f"a number that says more about Yishun than it does about the team cataloguing it. "
                        f"Incident #{milestone}: {incident_title}. "
                        f"The archive continues. Source: {source_url}."
                    )
                    _queue_milestone_post(
                        client, title, summary, "clown", 1,
                        "incident_count", milestone, f"INCIDENT #{milestone}",
                        source_url, incident_title, today_str,
                    )
                    _log_milestone(client, "incident_count", milestone, source_url, today_str)
                    triggered.append(f"incident_count ({milestone})")
    except Exception as exc:
        logger.warning("Herald: incident count check failed: %s", exc)


def _check_first_of_year(
    client,
    source_url: str,
    incident_title: str,
    today_str: str,
    triggered: list,
) -> None:
    """Check if this is the first incident of the calendar year."""
    try:
        year = int(today_str[:4])
        if _already_logged(client, "first_of_year", year=year):
            return

        res = (
            client.table("incidents")
            .select("id", count="exact")
            .eq("is_published", True)
            .gte("published_at", f"{year}-01-01T00:00:00Z")
            .execute()
        )
        if (res.count or 0) == 0:
            title = f"Yishun wastes no time in {year}."
            summary = (
                f"Yishun Again logs its first incident of {year}, confirming that the estate wasted no time. "
                f"The archive opens the year with: {incident_title}. "
                f"Source: {source_url}."
            )
            _queue_milestone_post(
                client, title, summary, "clown", 1,
                "first_of_year", year, f"FIRST OF YEAR {year}",
                source_url, incident_title, today_str,
            )
            _log_milestone(client, "first_of_year", year, source_url, today_str)
            triggered.append(f"first_of_year ({year})")
    except Exception as exc:
        logger.warning("Herald: first-of-year check failed: %s", exc)


# ── Public API ────────────────────────────────────────────────────────────────

def check_milestones(
    draft: dict,
    queue_id: str,
    source_url: str,
    incident_title: str,
    supabase_client,
) -> dict:
    """
    Check all milestone thresholds after a war_room_queue insert.

    Args:
        draft:            Stage 2 output dict — must include 'deaths' field.
        queue_id:         The war_room_queue row ID just inserted.
        source_url:       Primary source URL of the triggering item.
        incident_title:   Proposed title of the triggering item.
        supabase_client:  Supabase admin client (bypasses RLS).

    Returns:
        {"triggered": [list of triggered milestone names]}
    """
    deaths    = draft.get("deaths")   # None = not mentioned; 0 = confirmed none; N = confirmed
    today_str = _today_sgt().isoformat()
    triggered: list[str] = []

    logger.info("Herald: checking milestones — deaths=%r queue_id=%s", deaths, queue_id)

    try:
        _check_death_streak(
            supabase_client, deaths, queue_id, source_url, incident_title, today_str, triggered
        )
        _check_chaos_record(
            supabase_client, draft, source_url, incident_title, today_str, triggered
        )
        _check_incident_count(
            supabase_client, source_url, incident_title, today_str, triggered
        )
        _check_first_of_year(
            supabase_client, source_url, incident_title, today_str, triggered
        )
    except Exception as exc:
        logger.error("Herald: unhandled error: %s", exc)

    if triggered:
        logger.info("Herald: triggered — %s", ", ".join(triggered))
    else:
        logger.debug("Herald: no milestones triggered this run")

    return {"triggered": triggered}


# ── Offline test ─────────────────────────────────────────────────────────────
# Run: python packages/agents/orchestrator/herald_agent.py
# Uses an in-memory mock client — no real Supabase connection required.

class _MockResult:
    def __init__(self, data=None, count=None):
        self.data  = data  if data  is not None else []
        self.count = count


class _MockQuery:
    """Chainable query builder over an in-memory table."""

    def __init__(self, name: str, rows: list, store: dict):
        self._name    = name
        self._rows    = [dict(r) for r in rows]
        self._store   = store        # reference to _MockClient._tables for insert/update
        self._filters: list         = []
        self._order_col: str | None = None
        self._order_desc            = False
        self._limit_n: int | None   = None
        self._count_mode: str | None = None
        self._insert_row: dict | None = None
        self._update_row: dict | None = None
        self._action                = "select"

    def select(self, cols: str = "*", count: str | None = None):
        self._count_mode = count
        return self

    def insert(self, row: dict):
        self._action = "insert"
        self._insert_row = row
        return self

    def update(self, row: dict):
        self._action = "update"
        self._update_row = row
        return self

    def eq(self, col: str, val):
        self._filters.append(("eq", col, val))
        return self

    def gte(self, col: str, val):
        self._filters.append(("gte", col, val))
        return self

    def lt(self, col: str, val):
        self._filters.append(("lt", col, val))
        return self

    def order(self, col: str, desc: bool = False):
        self._order_col  = col
        self._order_desc = desc
        return self

    def limit(self, n: int):
        self._limit_n = n
        return self

    def single(self):
        self._limit_n = 1
        return self

    def execute(self) -> _MockResult:
        if self._action == "insert":
            row = self._insert_row or {}
            print(f"  [INSERT -> {self._name}]")
            for k, v in row.items():
                if k == "raw_content":
                    print(f"    raw_content.milestone_label: {v.get('milestone_label')!r}")
                    print(f"    raw_content.milestone_type:  {v.get('milestone_type')!r}")
                elif k not in ("proposed_summary",):
                    print(f"    {k}: {v!r}")
            self._store.setdefault(self._name, []).append(row)
            return _MockResult()

        if self._action == "update":
            keys = list((self._update_row or {}).keys())
            print(f"  [UPDATE -> {self._name}] fields={keys}")
            if "raw_content" in (self._update_row or {}):
                rc = self._update_row["raw_content"]
                print(f"    milestone_label: {rc.get('milestone_label')!r}")
                print(f"    milestone_type:  {rc.get('milestone_type')!r}")
                print(f"    milestone_value: {rc.get('milestone_value')!r}")
            return _MockResult()

        # Select — apply filters and ordering
        rows = self._rows[:]
        for op, col, val in self._filters:
            if op == "eq":
                rows = [r for r in rows if r.get(col) == val]
            elif op == "gte":
                rows = [r for r in rows if r.get(col) is not None and r.get(col) >= val]
            elif op == "lt":
                rows = [r for r in rows if r.get(col) is not None and r.get(col) < val]

        if self._order_col:
            rows.sort(
                key=lambda r: (r.get(self._order_col) is None, r.get(self._order_col) or ""),
                reverse=self._order_desc,
            )

        if self._limit_n:
            rows = rows[:self._limit_n]

        count = len(rows) if self._count_mode == "exact" else None
        return _MockResult(data=rows, count=count)


class _MockClient:
    """In-memory Supabase client for offline testing."""

    def __init__(self, last_death_days_ago: int = 80, published_count: int = 5):
        now = datetime.now(timezone.utc)

        death_pub = (now - timedelta(days=last_death_days_ago)).isoformat()

        incidents = [{
            "id":           "inc-death-0",
            "published_at": death_pub,
            "incident_date": death_pub[:10],
            "deaths":        1,
            "is_published":  True,
        }]
        for i in range(published_count - 1):
            pub = (now - timedelta(days=last_death_days_ago - 5 - i * 3)).isoformat()
            incidents.append({
                "id":           f"inc-{i}",
                "published_at": pub,
                "incident_date": pub[:10],
                "deaths":        None,
                "is_published":  True,
            })

        self._tables: dict = {
            "incidents": incidents,
            "milestones": [],
            "chaos_index_snapshots": [
                {"score_alltime": 38.0,
                 "snapshot_at": (now - timedelta(days=20)).isoformat()},
                {"score_alltime": 45.5,
                 "snapshot_at": (now - timedelta(days=1)).isoformat()},
            ],
            "war_room_queue": [{
                "id": "test-queue-id",
                "raw_content": {
                    "deaths": 1,
                    "title":  "Man found dead in Yishun void deck",
                },
            }],
        }

    def table(self, name: str) -> _MockQuery:
        return _MockQuery(name, self._tables.get(name, []), self._tables)


def _run_test() -> None:
    import sys
    print("\n" + "=" * 64)
    print("Herald Agent -- Offline Test")
    print("Scenario: death incident with chaos record check")
    print("=" * 64 + "\n")

    mock = _MockClient(last_death_days_ago=80, published_count=5)

    death_draft = {
        "title":             "Man found dead in Yishun void deck",
        "classification":    "dagger",
        "severity":          5,
        "deaths":            1,
        "injuries":          None,
        "confidence":        0.95,
        "chaos_contribution": 15.0,
        "hype_meter":        2,
    }

    print("Draft:   deaths=1, classification=dagger, severity=5")
    print("Context: chaos_index_snapshots has a new high (38->45.5)\n")
    print("Actions taken:")

    result = check_milestones(
        draft           = death_draft,
        queue_id        = "test-queue-id",
        source_url      = "https://www.channelnewsasia.com/singapore/yishun-man-found-dead-void-deck",
        incident_title  = "Man found dead in Yishun void deck",
        supabase_client = mock,
    )

    print("\n" + "-" * 64)
    print(f"Milestones triggered: {result['triggered']}")

    print("\n" + "-" * 64)
    print("Second run (same draft, all milestones already logged):")
    result2 = check_milestones(
        draft           = death_draft,
        queue_id        = "test-queue-id",
        source_url      = "https://www.channelnewsasia.com/singapore/yishun-man-found-dead-void-deck",
        incident_title  = "Man found dead in Yishun void deck",
        supabase_client = mock,
    )
    print(f"Milestones triggered: {result2['triggered']}  (empty = deduplication OK)")

    print("\n" + "=" * 64)
    print("Test complete.")
    print("=" * 64 + "\n")

    # Assertions
    assert "chaos_record (score=45)" in result["triggered"], \
        "FAIL: chaos_record not detected"
    assert result2["triggered"] == [], \
        "FAIL: deduplication did not prevent double-firing"
    print("All assertions passed.")


if __name__ == "__main__":
    import logging as _logging
    _logging.basicConfig(level=_logging.INFO, format="%(levelname)s: %(message)s")
    _run_test()
