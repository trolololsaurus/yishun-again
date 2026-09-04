"""
Monthly orchestrator report (req #13).

    from ops.monthly_report import run
    run()                                   # 30 days ending yesterday (SGT)
    run(period_end=date(2026, 6, 30))       # backfill a month that was missed
    run(period_end=date(2026, 6, 30), force=True)   # regenerate and overwrite

Runs on the 1st. Reads the seven ops tables the fleet writes to, folds them into
one `monthly_reports` row (structured JSONB + a narrative), and alerts it.

WHAT THIS IS FOR. The operator does not watch the pipeline; that is the point of
the autonomy work. So once a month the pipeline has to report to them, and the
first question is never "how many rows are in the database" — it is "is this
thing getting better or worse, and did it save me any work". Hence:
  * every headline number is paired with the same number for the previous 30
    days, and `summary_text` leads with the DELTA, not the total;
  * the operator section computes review-avoided, the one number that says
    whether the autonomy gate is earning its keep.

DESIGN RULES (same contract as the rest of ops/):
  * NEVER raises to the caller. Every read is wrapped; a missing table degrades
    that one section to {"available": false, "reason": ...} and the rest of the
    report still generates. A month with no data produces a valid report that
    says so.
  * Idempotent. monthly_reports has UNIQUE (period_start, period_end): an
    existing row for the period short-circuits (no second row, no second
    alert) unless force=True, and the write itself is an upsert so even a
    concurrent double-trigger cannot duplicate.

A note on windows: the query boundaries are UTC, matching how every timestamptz
in these tables was written. The War Room renders them in SGT. The DEFAULT
`period_end` (when the daily cadence step calls `run()` with none) is anchored
to the SGT calendar date instead — see `window_for()` for why a UTC-anchored
default sent two monthly reports on the same 1st.
"""

import logging
from datetime import date, datetime, timedelta, timezone

from ops.activity import AgentRun, _client, agent_enabled
from ops.notify import footer, notify, war_room_url

logger = logging.getLogger(__name__)

AGENT = "monthly_report"
WINDOW_DAYS = 30
SGT = timezone(timedelta(hours=8))

# What one queue card costs a human end-to-end. A stated assumption, not a
# measurement: the card count is the fact, the minutes are the translation.
MINUTES_PER_REVIEW = 3

# Enough to cover a 30-day window of the busiest table (agent_events) without
# an unbounded fetch. If a month ever exceeds it the report says so rather than
# silently reporting a truncated month.
_ROW_LIMIT = 5000

_REVIEW_ACTIONS = ("approve", "edit_approve", "reject")

# Worst-first ranking for health. 'unknown' outranks 'ok' because a component
# that stopped answering is not a component that is fine.
_STATUS_RANK = {"ok": 0, "unknown": 1, "degraded": 2, "down": 3}


# ── coercion helpers (every one of these tolerates junk) ────────────────────

def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _num(value):
    """float or None — DECIMAL columns arrive as float, str or None."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round(value, places: int = 3):
    return None if value is None else round(value, places)


def _as_dict(value) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        import json
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except ValueError:
            return {}
    return {}


def _coerce_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _share(part: int, whole: int):
    return round(part / whole, 3) if whole else None


def _unavailable(reason: str) -> dict:
    return {"available": False, "reason": reason}


# ── window ─────────────────────────────────────────────────────────────────

def window_for(period_end=None, *, now: datetime | None = None) -> tuple[date, date]:
    """
    (period_start, period_end), inclusive both ends, WINDOW_DAYS long.

    Default end is yesterday IN SGT: a report generated at 00:05 on the 1st
    must not claim to cover a day that is five minutes old.

    Anchored to SGT, not `datetime.now(timezone.utc).date()`, because the
    cadence itself is SGT (`ops/daily.py::sgt_today`) and the daily chain runs
    TWICE on the 1st (02:58 and 14:58 SGT) with no period_end passed either
    time — see the module `run()` docstring's idempotence claim. Those two SGT
    passes straddle UTC midnight (02:58 SGT = 18:58 UTC the PREVIOUS day; 14:58
    SGT = 06:58 UTC the SAME day), so a UTC-anchored "yesterday" computed the
    two passes' windows one day apart — different `(period_start, period_end)`,
    which defeated both the DB's UNIQUE constraint and notify()'s dedup_key and
    sent two near-identical monthly reports on the same 1st (observed live,
    2026-09-01: 2026-08-01..08-30 at the 02:58 pass, 2026-08-02..08-31 at
    14:58). Pinning to the SGT calendar date makes both passes agree.

    `now` is injectable (keyword-only, defaults to the real clock) so a test
    can pin the exact UTC instants either side of the straddle and assert they
    agree — the same pattern as `ops/daily.py::sgt_today`.
    """
    sgt_yesterday = (now or datetime.now(timezone.utc)).astimezone(SGT).date() - timedelta(days=1)
    end = _coerce_date(period_end) or sgt_yesterday
    return end - timedelta(days=WINDOW_DAYS - 1), end


def _bounds(start: date, end: date) -> tuple[str, str]:
    """ISO half-open [start 00:00Z, end+1d 00:00Z) — end date fully included."""
    lo = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)
    hi = datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    return lo.isoformat(), hi.isoformat()


def _fetch_window(client, table, columns, time_column, lo, hi, warnings) -> list | None:
    """
    Rows in [lo, hi). Returns None — not [] — when the table cannot be read, so
    "a quiet month" and "the table is gone" never render as the same thing.
    """
    try:
        res = (client.table(table).select(columns)
               .gte(time_column, lo).lt(time_column, hi)
               .limit(_ROW_LIMIT).execute())
        rows = res.data or []
    except Exception as exc:                      # noqa: BLE001 - see module docstring
        warnings.append(f"{table}: unreadable ({exc})")
        logger.warning("monthly_report: %s unreadable: %s", table, exc)
        return None
    if len(rows) >= _ROW_LIMIT:
        warnings.append(f"{table}: hit the {_ROW_LIMIT}-row cap — totals below are a floor, not a count")
    return rows


# ── section aggregators (pure — rows in, JSON-safe dict out) ────────────────

def summarise_ingestion(rows) -> dict:
    if rows is None:
        return _unavailable("pipeline_run_history could not be read")

    live = [r for r in rows if not r.get("dry_run")]
    per_source: dict[str, dict] = {}
    degraded_passes = 0
    total_queued = 0

    for row in live:
        if row.get("degraded"):
            degraded_passes += 1
        total_queued += _int(row.get("total_queued"))
        for src in (_as_dict(row.get("report")).get("per_source") or []):
            if not isinstance(src, dict):
                continue
            name = src.get("name") or "(unknown)"
            agg = per_source.setdefault(name, {
                "source": name, "passes": 0, "fetched": 0, "fresh": 0, "novel": 0,
                "queued": 0, "blocked": 0, "unavailable": 0, "degraded": 0,
                "last_reason": None,
            })
            agg["passes"] += 1
            for key in ("fetched", "fresh", "novel", "queued"):
                agg[key] += _int(src.get(key))
            status = src.get("status")
            if status in ("blocked", "unavailable", "degraded"):
                agg[status] += 1
                agg["last_reason"] = src.get("reason") or agg["last_reason"]

    ordered = sorted(per_source.values(), key=lambda s: (-s["queued"], -s["fetched"], s["source"]))
    return {
        "available":       True,
        "passes":          len(live),
        "total_queued":    total_queued,
        "degraded_passes": degraded_passes,
        "degraded_rate":   _share(degraded_passes, len(live)),
        "per_source":      ordered,
        "sources_blocked": [s["source"] for s in ordered if s["blocked"] or s["unavailable"]],
    }


def summarise_publishing(incidents, signals) -> dict:
    if incidents is None:
        return _unavailable("incidents could not be read")

    # decided_by='agent' is the authoritative marker; action='auto_approve' is
    # the belt-and-braces check for rows written before migration 011 added the
    # column's default.
    auto_ids = {
        s.get("incident_id") for s in (signals or [])
        if s.get("decided_by") == "agent" or s.get("action") == "auto_approve"
    }
    auto_ids.discard(None)

    by_class: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    severities: list[int] = []
    auto = 0

    for inc in incidents:
        cls = inc.get("classification") or "unknown"
        by_class[cls] = by_class.get(cls, 0) + 1
        sev = _int(inc.get("severity"), 0)
        by_severity[str(sev)] = by_severity.get(str(sev), 0) + 1
        if sev:
            severities.append(sev)
        if inc.get("id") in auto_ids:
            auto += 1

    published = len(incidents)
    recent = sorted(incidents, key=lambda i: (i.get("published_at") or ""), reverse=True)
    return {
        "available":          True,
        "published":          published,
        "auto_published":     auto,
        "operator_approved":  published - auto,
        "auto_share":         _share(auto, published),
        "split_available":    signals is not None,
        "by_classification":  dict(sorted(by_class.items(), key=lambda kv: -kv[1])),
        "by_severity":        dict(sorted(by_severity.items())),
        "mean_severity":      _round(sum(severities) / len(severities), 2) if severities else None,
        "recent": [
            {"title": i.get("title"), "slug": i.get("slug"),
             "classification": i.get("classification"), "severity": _int(i.get("severity")),
             "published_at": i.get("published_at"), "auto": i.get("id") in auto_ids}
            for i in recent[:10]
        ],
    }


def summarise_operator(signals) -> dict:
    if signals is None:
        return _unavailable("training_signals could not be read")

    by_action: dict[str, int] = {}
    agent_decisions = 0
    for sig in signals:
        action = sig.get("action") or "unknown"
        by_action[action] = by_action.get(action, 0) + 1
        if sig.get("decided_by") == "agent" or action == "auto_approve":
            agent_decisions += 1

    reviewed = sum(by_action.get(a, 0) for a in _REVIEW_ACTIONS)
    reverted = by_action.get("auto_publish_reverted", 0)
    # A reverted auto-publish cost the operator a review anyway (plus the
    # cleanup), so it does not count as saved.
    net_saved = max(0, agent_decisions - reverted)

    return {
        "available":          True,
        "by_action":          dict(sorted(by_action.items(), key=lambda kv: -kv[1])),
        "operator_decisions": reviewed,
        "approve":            by_action.get("approve", 0),
        "edit_approve":       by_action.get("edit_approve", 0),
        "reject":             by_action.get("reject", 0),
        "unpublish":          by_action.get("unpublish", 0),
        "agent_decisions":    agent_decisions,
        "reverted":           reverted,
        "reviews_saved":      agent_decisions,
        "net_reviews_saved":  net_saved,
        "minutes_saved":      net_saved * MINUTES_PER_REVIEW,
        "minutes_per_review": MINUTES_PER_REVIEW,
        "autonomy_share":     _share(agent_decisions, agent_decisions + reviewed),
    }


def summarise_learning(snapshots, previous) -> dict:
    if snapshots is None:
        return _unavailable("learning_snapshots could not be read")
    if not snapshots:
        return {"available": True, "captured": False,
                "reason": "no learning snapshot was captured in this period"}

    latest = max(snapshots, key=lambda s: (s.get("captured_at") or ""))
    prev = previous[0] if previous else None

    agreement = _num(latest.get("agreement_rate"))
    prev_agreement = _num(prev.get("agreement_rate")) if prev else None
    vs_previous = (_round(agreement - prev_agreement)
                   if agreement is not None and prev_agreement is not None else None)

    return {
        "available":             True,
        "captured":              True,
        "snapshots":             len(snapshots),
        "captured_at":           latest.get("captured_at"),
        "sample_count":          _int(latest.get("sample_count")),
        "agreement_rate":        _num(latest.get("agreement_rate")),
        "agreement_delta":       _num(latest.get("agreement_delta")),
        "mean_confidence":       _num(latest.get("mean_confidence")),
        "confidence_delta":      _num(latest.get("confidence_delta")),
        "edit_rate":             _num(latest.get("edit_rate")),
        "reject_rate":           _num(latest.get("reject_rate")),
        "auto_publish_count":    _int(latest.get("auto_publish_count")),
        "auto_publish_reverted": _int(latest.get("auto_publish_reverted")),
        "verdict":               latest.get("verdict") or "insufficient_data",
        "previous": None if not prev else {
            "captured_at":    prev.get("captured_at"),
            "agreement_rate": prev_agreement,
            "verdict":        prev.get("verdict"),
        },
        "agreement_vs_previous_month": vs_previous,
    }


def summarise_reliability(runs, events) -> dict:
    if runs is None and events is None:
        return _unavailable("agent_runs and agent_events could not be read")

    by_agent: dict[str, dict] = {}
    totals = {"ok": 0, "degraded": 0, "failed": 0, "running": 0}
    durations: dict[str, list[int]] = {}

    for row in (runs or []):
        name = row.get("agent") or "(unknown)"
        agg = by_agent.setdefault(name, {"agent": name, "runs": 0, "ok": 0,
                                         "degraded": 0, "failed": 0, "running": 0,
                                         "avg_duration_ms": None})
        agg["runs"] += 1
        status = row.get("status")
        if status in totals:
            agg[status] += 1
            totals[status] += 1
        ms = row.get("duration_ms")
        if ms is not None:
            durations.setdefault(name, []).append(_int(ms))

    for name, agg in by_agent.items():
        samples = durations.get(name) or []
        if samples:
            agg["avg_duration_ms"] = int(sum(samples) / len(samples))

    event_counts: dict[tuple, int] = {}
    levels: dict[str, int] = {}
    for row in (events or []):
        level = row.get("level") or "info"
        levels[level] = levels.get(level, 0) + 1
        if level in ("error", "anomaly"):
            key = (row.get("event") or "(unknown)", level)
            event_counts[key] = event_counts.get(key, 0) + 1

    top = sorted(event_counts.items(), key=lambda kv: (-kv[1], kv[0][0]))[:10]

    return {
        "available":     True,
        "runs":          len(runs or []),
        "runs_readable": runs is not None,
        "ok":            totals["ok"],
        "degraded":      totals["degraded"],
        "failed":        totals["failed"],
        "running":       totals["running"],
        "by_agent":      sorted(by_agent.values(), key=lambda a: (-a["failed"], -a["degraded"], -a["runs"])),
        "events":        {"error": levels.get("error", 0), "anomaly": levels.get("anomaly", 0)},
        "top_events":    [{"event": e, "level": lvl, "count": n} for (e, lvl), n in top],
    }


def summarise_health(checks) -> dict:
    if checks is None:
        return _unavailable("backend_health_checks could not be read")

    components: dict[str, dict] = {}
    cost_guard = None

    for row in checks:
        name = row.get("component") or "(unknown)"
        status = row.get("status") or "unknown"
        checked_at = row.get("checked_at") or ""
        comp = components.setdefault(name, {
            "component": name, "checks": 0, "worst_status": "ok",
            "last_status": None, "last_checked_at": None, "message": None,
        })
        comp["checks"] += 1
        if _STATUS_RANK.get(status, 1) > _STATUS_RANK.get(comp["worst_status"], 0):
            comp["worst_status"] = status
        if checked_at >= (comp["last_checked_at"] or ""):
            comp["last_checked_at"] = checked_at or None
            comp["last_status"] = status
            comp["message"] = row.get("message")
        if name == "cost_guard" and checked_at >= ((cost_guard or {}).get("checked_at") or ""):
            cost_guard = {"status": status, "checked_at": checked_at or None,
                          "message": row.get("message"), "detail": _as_dict(row.get("detail"))}

    ordered = sorted(components.values(),
                     key=lambda c: (-_STATUS_RANK.get(c["worst_status"], 1), c["component"]))
    worst = ordered[0]["worst_status"] if ordered else None

    return {
        "available":    True,
        "checks":       len(checks),
        "components":   ordered,
        "worst_status": worst,
        "cost_guard":   cost_guard,
    }


def summarise_notifications(rows) -> dict:
    if rows is None:
        return _unavailable("notifications could not be read")

    by_kind: dict[str, dict] = {}
    sent = 0
    for row in rows:
        kind = row.get("kind") or "(unknown)"
        status = row.get("status") or "pending"
        agg = by_kind.setdefault(kind, {"kind": kind, "total": 0, "sent": 0, "suppressed": 0,
                                        "failed": 0, "disabled": 0, "pending": 0})
        agg["total"] += 1
        if status in agg:
            agg[status] += 1
        if status == "sent":
            sent += 1

    return {
        "available": True,
        "total":     len(rows),
        "sent":      sent,
        "by_kind":   sorted(by_kind.values(), key=lambda k: -k["total"]),
    }


# ── collection ─────────────────────────────────────────────────────────────

def _headline(client, start: date, end: date, warnings: list) -> dict:
    """
    The four numbers the narrative compares month-over-month. Kept deliberately
    cheap — the previous period is context, not a second full report.
    """
    lo, hi = _bounds(start, end)
    incidents = _fetch_window(client, "incidents", "id,published_at",
                              "published_at", lo, hi, warnings)
    signals = _fetch_window(client, "training_signals", "action,decided_by,created_at",
                            "created_at", lo, hi, warnings)
    passes = _fetch_window(client, "pipeline_run_history", "dry_run,total_queued",
                           "ran_at", lo, hi, warnings)

    live = [p for p in (passes or []) if not p.get("dry_run")]
    auto = sum(1 for s in (signals or [])
               if s.get("decided_by") == "agent" or s.get("action") == "auto_approve")
    return {
        "start":              start.isoformat(),
        "end":                end.isoformat(),
        "published":          len(incidents) if incidents is not None else None,
        "auto_published":     auto if signals is not None else None,
        "operator_decisions": (sum(1 for s in signals if s.get("action") in _REVIEW_ACTIONS)
                               if signals is not None else None),
        "total_queued":       sum(_int(p.get("total_queued")) for p in live) if passes is not None else None,
        "passes":             len(live) if passes is not None else None,
    }


def collect(client, start: date, end: date, trigger: str = "scheduler") -> dict:
    """Build the full report JSONB for [start, end]. Never raises."""
    warnings: list[str] = []
    lo, hi = _bounds(start, end)

    passes = _fetch_window(client, "pipeline_run_history", "ran_at,dry_run,degraded,total_queued,report",
                           "ran_at", lo, hi, warnings)
    # published_at is nulled on unpublish, so an incident published and then
    # pulled inside the window drops out here by design — this counts what is
    # live, and the operator's unpublish shows up in the operator section.
    incidents = _fetch_window(client, "incidents", "id,title,slug,classification,severity,published_at",
                              "published_at", lo, hi, warnings)
    signals = _fetch_window(client, "training_signals", "action,decision,decided_by,incident_id,created_at",
                            "created_at", lo, hi, warnings)
    snapshots = _fetch_window(client, "learning_snapshots",
                              "captured_at,sample_count,mean_confidence,confidence_delta,"
                              "agreement_rate,agreement_delta,edit_rate,reject_rate,"
                              "auto_publish_count,auto_publish_reverted,verdict",
                              "captured_at", lo, hi, warnings)
    runs = _fetch_window(client, "agent_runs", "agent,status,duration_ms,started_at",
                         "started_at", lo, hi, warnings)
    events = _fetch_window(client, "agent_events", "level,event,agent,created_at",
                           "created_at", lo, hi, warnings)
    checks = _fetch_window(client, "backend_health_checks", "component,status,message,detail,checked_at",
                           "checked_at", lo, hi, warnings)
    notifications = _fetch_window(client, "notifications", "kind,status,created_at",
                                  "created_at", lo, hi, warnings)

    previous_snapshot = _latest_snapshot_before(client, lo, warnings)

    prev_start = start - timedelta(days=WINDOW_DAYS)
    previous = _headline(client, prev_start, start - timedelta(days=1), warnings)

    publishing = summarise_publishing(incidents, signals)
    operator = summarise_operator(signals)
    ingestion = summarise_ingestion(passes)

    current = {
        "published":          publishing.get("published"),
        "auto_published":     publishing.get("auto_published"),
        "operator_decisions": operator.get("operator_decisions"),
        "total_queued":       ingestion.get("total_queued"),
        "passes":             ingestion.get("passes"),
    }
    changes = {
        key: (current[key] - previous[key])
        for key in current
        if isinstance(current.get(key), int) and isinstance(previous.get(key), int)
    }

    return {
        "period": {
            "start":        start.isoformat(),
            "end":          end.isoformat(),
            "days":         WINDOW_DAYS,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "trigger":      trigger,
        },
        "ingestion":       ingestion,
        "publishing":      publishing,
        "operator":        operator,
        "learning":        summarise_learning(snapshots, previous_snapshot),
        "reliability":     summarise_reliability(runs, events),
        "health":          summarise_health(checks),
        "notifications":   summarise_notifications(notifications),
        "previous_period": previous,
        "changes":         changes,
        "warnings":        warnings,
    }


def _latest_snapshot_before(client, lo: str, warnings: list) -> list | None:
    """The last learning snapshot BEFORE the window — the month-over-month baseline."""
    try:
        res = (client.table("learning_snapshots")
               .select("captured_at,agreement_rate,mean_confidence,verdict")
               .lt("captured_at", lo)
               .order("captured_at", desc=True)
               .limit(1).execute())
        return res.data or []
    except Exception as exc:                      # noqa: BLE001
        warnings.append(f"learning_snapshots: no baseline ({exc})")
        return None


# ── narrative ──────────────────────────────────────────────────────────────

def _delta(now, before) -> str:
    if not isinstance(now, int) or not isinstance(before, int):
        return "no comparison available"
    diff = now - before
    if diff == 0:
        return f"level with the previous 30 days ({before})"
    return f"{'up' if diff > 0 else 'down'} {abs(diff)} vs {before} in the previous 30 days"


def _pct(value) -> str:
    return "—" if value is None else f"{value * 100:.0f}%"


def _fmt_day(iso: str) -> str:
    parsed = _coerce_date(iso)
    return parsed.strftime("%d %b %Y") if parsed else str(iso)


def build_summary_text(report: dict) -> str:
    """
    The 30-second read. Deltas first — a total tells the operator nothing they
    could not have guessed; the direction of travel is the whole point.
    """
    period = report.get("period") or {}
    pub = report.get("publishing") or {}
    op = report.get("operator") or {}
    learn = report.get("learning") or {}
    ing = report.get("ingestion") or {}
    rel = report.get("reliability") or {}
    health = report.get("health") or {}
    notes = report.get("notifications") or {}
    prev = report.get("previous_period") or {}

    lines = [
        "YISHUN AGAIN — MONTHLY REPORT",
        f"{_fmt_day(period.get('start'))} – {_fmt_day(period.get('end'))} "
        f"({period.get('days', WINDOW_DAYS)} days)",
        "",
        "WHAT CHANGED",
    ]

    if not pub.get("available"):
        lines.append(f"  No publishing data for this period ({pub.get('reason')}).")
    elif pub.get("published"):
        lines.append(f"  Published {pub['published']} incident(s) — {_delta(pub.get('published'), prev.get('published'))}.")
        if pub.get("auto_published"):
            lines.append(
                f"  {pub['auto_published']} published without review "
                f"({_pct(pub.get('auto_share'))} of the month) — "
                f"{_delta(pub.get('auto_published'), prev.get('auto_published'))}."
            )
        else:
            lines.append("  Nothing cleared the auto-publish gate — every incident went through you.")
    else:
        lines.append("  Nothing was published this period.")

    if op.get("available"):
        lines.append(
            f"  You made {op['operator_decisions']} review decision(s) "
            f"({op['approve']} approve / {op['edit_approve']} edit / {op['reject']} reject) — "
            f"{_delta(op.get('operator_decisions'), prev.get('operator_decisions'))}."
        )
        if op.get("reviews_saved"):
            saved = (f"  Review avoided on {op['reviews_saved']} card(s) "
                     f"(~{op['minutes_saved']} min at {op['minutes_per_review']} min/card)")
            if op.get("reverted"):
                saved += f", but {op['reverted']} auto-publish was reverted"
            lines.append(saved + ".")
        if op.get("unpublish"):
            lines.append(f"  {op['unpublish']} published incident(s) were pulled back down.")
    else:
        lines.append(f"  No operator data for this period ({op.get('reason')}).")

    lines += ["", "LEARNING"]
    if not learn.get("available"):
        lines.append(f"  Unavailable ({learn.get('reason')}).")
    elif not learn.get("captured"):
        lines.append("  No snapshot captured this period — the learning loop has nothing to compare.")
    else:
        vs = learn.get("agreement_vs_previous_month")
        drift = "" if vs is None else f" ({vs * 100:+.0f} pts vs last month)"
        lines.append(
            f"  Verdict: {str(learn.get('verdict', '')).upper()}. "
            f"Agreement {_pct(learn.get('agreement_rate'))}{drift} over {learn.get('sample_count')} sample(s)."
        )
        lines.append(
            f"  Mean confidence {learn.get('mean_confidence') if learn.get('mean_confidence') is not None else '—'}"
            f" (delta {learn.get('confidence_delta') if learn.get('confidence_delta') is not None else '—'}); "
            f"{learn.get('auto_publish_reverted', 0)} of {learn.get('auto_publish_count', 0)} auto-publishes reverted."
        )

    lines += ["", "PIPELINE"]
    if not ing.get("available"):
        lines.append(f"  Unavailable ({ing.get('reason')}).")
    elif not ing.get("passes"):
        lines.append("  No ingestion passes ran in this period.")
    else:
        lines.append(
            f"  {ing['passes']} pass(es), {ing['total_queued']} card(s) queued "
            f"({_delta(ing.get('total_queued'), prev.get('total_queued'))})."
        )
        lines.append(f"  {ing['degraded_passes']} degraded pass(es) ({_pct(ing.get('degraded_rate'))}).")
        if ing.get("sources_blocked"):
            lines.append(f"  Blocked or unavailable at least once: {', '.join(ing['sources_blocked'][:8])}.")
        top = [s for s in (ing.get("per_source") or []) if s.get("queued")][:3]
        if top:
            lines.append("  Best-performing sources: "
                         + ", ".join(f"{s['source']} ({s['queued']})" for s in top) + ".")

    lines += ["", "RELIABILITY"]
    if not rel.get("available"):
        lines.append(f"  Unavailable ({rel.get('reason')}).")
    elif not rel.get("runs"):
        lines.append("  No agent runs recorded in this period.")
    else:
        lines.append(f"  {rel['runs']} agent run(s): {rel['ok']} ok, {rel['degraded']} degraded, "
                     f"{rel['failed']} failed, {rel['running']} never finished.")
        if rel.get("top_events"):
            lines.append("  Loudest events: "
                         + ", ".join(f"{e['event']} ×{e['count']}" for e in rel["top_events"][:5]) + ".")

    lines += ["", "HEALTH & COST"]
    if not health.get("available"):
        lines.append(f"  Unavailable ({health.get('reason')}).")
    elif not health.get("checks"):
        lines.append("  No health checks recorded in this period.")
    else:
        worst = health.get("worst_status")
        offenders = [c["component"] for c in (health.get("components") or [])
                     if c.get("worst_status") == worst and worst != "ok"]
        lines.append(f"  Worst component status this period: {str(worst).upper()}"
                     + (f" ({', '.join(offenders[:4])})." if offenders else "."))
        cost = health.get("cost_guard")
        if cost:
            lines.append(f"  Cost guard: {str(cost.get('status', '')).upper()}"
                         + (f" — {cost['message']}." if cost.get("message") else "."))

    if notes.get("available") and notes.get("total"):
        kinds = ", ".join(f"{k['kind']} {k['sent']}" for k in (notes.get("by_kind") or [])[:5])
        lines += ["", f"ALERTS: {notes['sent']} sent of {notes['total']} logged ({kinds})."]

    if report.get("warnings"):
        lines += ["", "DATA GAPS"] + [f"  ! {w}" for w in report["warnings"][:8]]

    lines += ["", f"Full report: {war_room_url('/reports')}"]
    return "\n".join(lines)


# ── persistence ────────────────────────────────────────────────────────────

def _existing(client, start: date, end: date):
    try:
        res = (client.table("monthly_reports").select("id,emailed_at")
               .eq("period_start", start.isoformat())
               .eq("period_end", end.isoformat())
               .limit(1).execute())
        return (res.data or [None])[0]
    except Exception as exc:                      # noqa: BLE001
        # Cannot tell whether one exists -> proceed. The upsert below is the
        # real duplicate guard; this check only saves the work.
        logger.warning("monthly_report: existence check failed (%s) — generating anyway", exc)
        return None


def _upsert(client, start: date, end: date, report: dict, summary_text: str) -> bool:
    try:
        client.table("monthly_reports").upsert({
            "period_start": start.isoformat(),
            "period_end":   end.isoformat(),
            "report":       report,
            "summary_text": summary_text,
        }, on_conflict="period_start,period_end").execute()
        return True
    except Exception as exc:                      # noqa: BLE001
        logger.error("monthly_report: could not store report for %s..%s: %s", start, end, exc)
        return False


def _mark_emailed(client, start: date, end: date) -> None:
    # Keyed on the period, not the row id: upsert does not reliably return a
    # representation, and the period is the natural key anyway.
    try:
        client.table("monthly_reports").update(
            {"emailed_at": datetime.now(timezone.utc).isoformat()}
        ).eq("period_start", start.isoformat()).eq("period_end", end.isoformat()).execute()
    except Exception as exc:                      # noqa: BLE001
        logger.warning("monthly_report: could not stamp emailed_at: %s", exc)


# ── entry point ────────────────────────────────────────────────────────────

def run(supabase_client=None, period_end=None, force=False, trigger: str = "scheduler") -> dict:
    """
    Generate, store and alert the 30-day report. Never raises.

    Returns {"status": ok|exists|failed|disabled, "period_start", "period_end",
             "written", "notified", "notification_status", "published",
             "auto_published", "warnings": [...], "errors": int}.
    """
    start, end = window_for(period_end)
    stats = {
        "status":              "ok",
        "period_start":        start.isoformat(),
        "period_end":          end.isoformat(),
        "written":             False,
        "notified":                         False,
        "notification_status": None,
        "published":           0,
        "auto_published":      0,
        "warnings":            [],
        "errors":              0,
    }

    if not agent_enabled(AGENT):
        logger.warning("monthly_report: disabled via AGENT_DISABLED")
        stats["status"] = "disabled"
        return stats

    with AgentRun(AGENT, trigger=trigger) as arun:
        arun.stat("period", f"{start.isoformat()}..{end.isoformat()}")
        try:
            client = _client(supabase_client)
        except Exception as exc:                  # noqa: BLE001
            arun.fail(f"no Supabase client: {exc}")
            stats["status"] = "failed"
            stats["errors"] += 1
            stats["warnings"].append(str(exc))
            return stats

        try:
            existing = _existing(client, start, end)
            if existing and not force:
                arun.info("already_generated",
                          f"report for {start}..{end} exists — skipping (force=True to overwrite)")
                arun.set_summary(f"report for {start}..{end} already existed")
                stats["status"] = "exists"
                return stats

            report = collect(client, start, end, trigger=trigger)
            summary_text = build_summary_text(report)

            stats["warnings"] = list(report.get("warnings") or [])
            stats["published"] = _int((report.get("publishing") or {}).get("published"))
            stats["auto_published"] = _int((report.get("publishing") or {}).get("auto_published"))

            stats["written"] = _upsert(client, start, end, report, summary_text)
            if not stats["written"]:
                arun.error_("store_failed", f"report for {start}..{end} was generated but not stored")
                stats["status"] = "failed"
                stats["errors"] += 1
            else:
                arun.success("report_stored",
                             f"{start}..{end}: {stats['published']} published, "
                             f"{stats['auto_published']} of them autonomously")

            for warning in stats["warnings"]:
                arun.warn("data_gap", warning)

            # Emailed even when the store failed: the operator gets the month
            # either way, and the narrative is the part they actually read.
            result = notify(
                "monthly_report",
                f"Yishun Again — monthly report, {_fmt_day(start.isoformat())} – {_fmt_day(end.isoformat())}",
                summary_text + footer(),
                dedup_key=f"monthly_report:{start.isoformat()}:{end.isoformat()}",
                client=client,
            )
            stats["notified"] = result.get("status") == "sent"
            stats["notification_status"] = result.get("status")
            if stats["notified"] and stats["written"]:
                _mark_emailed(client, start, end)

            arun.stat("published", stats["published"])
            arun.stat("auto_published", stats["auto_published"])
            arun.stat("warnings", len(stats["warnings"]))
            arun.set_summary(
                f"{start}..{end}: {stats['published']} published, "
                f"{stats['auto_published']} autonomous, notified={stats.get('notification_status')}"
            )

        except Exception as exc:                  # noqa: BLE001 - see module docstring
            logger.exception("monthly_report: unexpected failure")
            arun.fail(f"{type(exc).__name__}: {exc}")
            stats["status"] = "failed"
            stats["errors"] += 1
            stats["warnings"].append(f"{type(exc).__name__}: {exc}")

    return stats
