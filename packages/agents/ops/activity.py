"""
Agent activity logging — success, failure and anomaly (req #7).

Every agent wraps its work in an AgentRun context manager:

    with AgentRun("supervisor") as run:
        run.success("source_ok", "CNA returned 3 items", source_name="cna")
        run.anomaly("zero_streak", "Stomp: 5 passes with 0 items", source_name="stomp")
        run.bump("sources_checked")

On exit the run row is closed out with a status derived from what was logged:
any error event -> 'degraded', an escaping exception -> 'failed', otherwise
'ok'. Callers can override with run.fail(...) / run.set_status(...).

DESIGN RULE — this module never raises. Not on a missing Supabase client, not
on a network blip, not on a bad payload. Observability must not be able to take
down the pipeline it observes. Every DB call is wrapped, and every event is
ALSO written to stdlib logging, so a Supabase outage degrades to Cloud Run logs
rather than silence.
"""

import logging
import os
import time
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

LEVELS = ("info", "success", "warning", "error", "anomaly")

# Events are flushed in batches to keep a 200-candidate pass from making 200
# extra round-trips. Anomalies and errors flush immediately — those are the
# ones a human may be paged about, and they must survive a hard crash.
_BATCH_SIZE = 25


def _client(explicit=None):
    """Return a Supabase client, or None. Never raises."""
    if explicit is not None:
        return explicit
    try:
        from classifiers.corroboration import get_supabase_client
        return get_supabase_client()
    except Exception as exc:                      # noqa: BLE001 - see module docstring
        logger.debug("activity: no Supabase client (%s) — logging to stdout only", exc)
        return None


class AgentRun:
    """
    One agent invocation. Use as a context manager.

    Works with no database: run_id falls back to a local UUID, events go to
    stdlib logging only, and nothing downstream notices.
    """

    def __init__(self, agent: str, trigger: str = "scheduler", client=None):
        self.agent = agent
        self.trigger = trigger if trigger in ("scheduler", "manual", "chained") else "manual"
        self.client = _client(client)
        self.run_id: str | None = None
        self.stats: dict = {}
        self.summary: str | None = None
        self.error: str | None = None
        self._status: str | None = None
        self._saw_error = False
        self._saw_anomaly = False
        self._pending: list[dict] = []
        self._t0 = time.monotonic()

    # ── lifecycle ──────────────────────────────────────────────────────────

    def __enter__(self) -> "AgentRun":
        self.run_id = str(uuid.uuid4())
        if self.client:
            try:
                self.client.table("agent_runs").insert({
                    "id":         self.run_id,
                    "agent":      self.agent,
                    "trigger":    self.trigger,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "status":     "running",
                }).execute()
            except Exception as exc:              # noqa: BLE001
                logger.warning("activity: could not open run row for %s: %s", self.agent, exc)
                self.client = None                # stop trying for the rest of the run
        logger.info("[%s] run started (id=%s trigger=%s)", self.agent, self.run_id, self.trigger)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc is not None:
            self.error = f"{exc_type.__name__}: {exc}"
            self._status = "failed"
            self.event("error", "run_crashed", self.error)
        self.close()
        return False        # never swallow the caller's exception

    def close(self) -> None:
        """Idempotent — safe to call directly when not using `with`."""
        if self.run_id is None:
            return
        self._flush()
        status = self._status or ("degraded" if (self._saw_error or self._saw_anomaly) else "ok")
        duration_ms = int((time.monotonic() - self._t0) * 1000)

        logger.info(
            "[%s] run finished status=%s duration=%dms stats=%s",
            self.agent, status, duration_ms, self.stats,
        )
        if self.client:
            try:
                self.client.table("agent_runs").update({
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "duration_ms": duration_ms,
                    "status":      status,
                    "summary":     self.summary,
                    "stats":       self.stats,
                    "error":       self.error,
                }).eq("id", self.run_id).execute()
            except Exception as exc:              # noqa: BLE001
                logger.warning("activity: could not close run row %s: %s", self.run_id, exc)
        self.run_id = None

    # ── event recording ────────────────────────────────────────────────────

    def event(self, level: str, event: str, message: str,
              source_name: str | None = None, **detail) -> None:
        """Record one activity event. Never raises."""
        if level not in LEVELS:
            level = "info"
        if level == "error":
            self._saw_error = True
        elif level == "anomaly":
            self._saw_anomaly = True

        log_at = {"error": logging.ERROR, "warning": logging.WARNING,
                  "anomaly": logging.WARNING}.get(level, logging.INFO)
        logger.log(log_at, "[%s] %s: %s%s", self.agent, event, message,
                   f" (source={source_name})" if source_name else "")

        self._pending.append({
            "run_id":      self.run_id,
            "agent":       self.agent,
            "level":       level,
            "event":       event[:200],
            "message":     message[:2000],
            "source_name": source_name,
            "detail":      _jsonable(detail),
        })
        # Errors and anomalies must survive a hard crash — flush now.
        if level in ("error", "anomaly") or len(self._pending) >= _BATCH_SIZE:
            self._flush()

    def info(self, event, message, **kw):    self.event("info", event, message, **kw)
    def success(self, event, message, **kw): self.event("success", event, message, **kw)
    def warn(self, event, message, **kw):    self.event("warning", event, message, **kw)
    def error_(self, event, message, **kw):  self.event("error", event, message, **kw)
    def anomaly(self, event, message, **kw): self.event("anomaly", event, message, **kw)

    def _flush(self) -> None:
        if not self._pending:
            return
        batch, self._pending = self._pending, []
        if not self.client:
            return
        try:
            self.client.table("agent_events").insert(batch).execute()
        except Exception as exc:                  # noqa: BLE001
            logger.warning("activity: dropped %d event(s) for %s: %s", len(batch), self.agent, exc)

    # ── stats + status ─────────────────────────────────────────────────────

    def bump(self, key: str, n: int = 1) -> None:
        self.stats[key] = self.stats.get(key, 0) + n

    def stat(self, key: str, value) -> None:
        self.stats[key] = value

    def set_summary(self, text: str) -> None:
        self.summary = text[:2000]

    def set_status(self, status: str) -> None:
        if status in ("running", "ok", "degraded", "failed"):
            self._status = status

    def fail(self, message: str) -> None:
        self.error = message[:2000]
        self._status = "failed"


def _jsonable(value):
    """Coerce to something Supabase can serialise. Never raises."""
    import json
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return {"repr": repr(value)[:2000]}


# ── read helpers (used by the maintenance agent + War Room) ────────────────

def recent_events(hours: int = 24, levels: tuple = ("error", "anomaly"),
                  limit: int = 200, client=None) -> list[dict]:
    """Recent activity events at the given levels. Returns [] on any failure."""
    from datetime import timedelta
    c = _client(client)
    if not c:
        return []
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    try:
        res = (c.table("agent_events")
               .select("created_at,agent,level,event,message,source_name,detail")
               .gte("created_at", since)
               .in_("level", list(levels))
               .order("created_at", desc=True)
               .limit(limit).execute())
        return res.data or []
    except Exception as exc:                      # noqa: BLE001
        logger.warning("activity: recent_events failed: %s", exc)
        return []


def recent_runs(hours: int = 24, limit: int = 100, client=None) -> list[dict]:
    """Recent agent runs. Returns [] on any failure."""
    from datetime import timedelta
    c = _client(client)
    if not c:
        return []
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    try:
        res = (c.table("agent_runs")
               .select("id,agent,trigger,started_at,finished_at,duration_ms,status,summary,stats,error")
               .gte("started_at", since)
               .order("started_at", desc=True)
               .limit(limit).execute())
        return res.data or []
    except Exception as exc:                      # noqa: BLE001
        logger.warning("activity: recent_runs failed: %s", exc)
        return []


def stale_runs(older_than_minutes: int = 90, client=None) -> list[dict]:
    """
    Runs still marked 'running' long after they should have finished — i.e. the
    container died mid-pass. A crashed agent leaves no error row, so this
    absence-shaped failure is the only trace it leaves.
    """
    from datetime import timedelta
    c = _client(client)
    if not c:
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=older_than_minutes)).isoformat()
    try:
        res = (c.table("agent_runs")
               .select("id,agent,started_at,trigger")
               .eq("status", "running")
               .lt("started_at", cutoff)
               .order("started_at", desc=True)
               .limit(50).execute())
        return res.data or []
    except Exception as exc:                      # noqa: BLE001
        logger.warning("activity: stale_runs failed: %s", exc)
        return []


def agent_enabled(name: str, default: bool = True) -> bool:
    """
    Kill switch. `AGENT_DISABLED=supervisor,integrity` in the Cloud Run env
    turns agents off without a redeploy — the thing you want at 3am when one
    agent is misbehaving and you do not want to lose the whole pass.
    """
    disabled = os.getenv("AGENT_DISABLED", "")
    return name not in {x.strip() for x in disabled.split(",") if x.strip()} and default
