"""
The daily orchestrator (req #13) — one entry point the scheduler calls at 14:58 SGT.

Runs the whole fleet in a deliberate order and returns a single report.

## Order is not arbitrary

    1. ingestion        produce candidate drafts
    2. auto_publish     publish >= threshold; email about everything below (#3, #4)
    3. integrity        dupes + hallucinations — AFTER publish, so it audits what
                        actually went live, not what was merely proposed (#10)
    4. supervisor       scraper fleet health — AFTER ingestion, so it grades this
                        pass rather than yesterday's (#9)
    5. learning_monitor rebuild source_reputation, snapshot the deltas (#5)
    6. backend_health   Supabase / R2 / API / cost guard (#12)
    7. maintenance      reads everything the six steps above logged and mails ONE
                        digest — so it must run LAST or it reports stale news (#11)
    8. monthly_report   1st of the month only; last 30 days (#13)

## Failure isolation

Every step is wrapped. A crash in step 3 must not cost the operator steps 4-8 —
the monitoring agents are most valuable precisely when something has gone wrong.
A failed step is recorded in the report with its traceback and the chain
continues. The only genuinely fatal condition is being unable to reach Supabase
at all, which every step reports independently anyway.

## Why this exists instead of eight scheduler entries

Cloud Run scales to zero. In-process APScheduler needs min-instances=1 and
CPU-always-allocated to fire reliably — about $15-25/month to run jobs that
occupy ~15 minutes of CPU a day. One Cloud Scheduler ping to one endpoint keeps
the service at zero instances the other 23h 45m, which is the whole of req #12's
cost answer. It also makes the ordering above explicit and testable, rather than
an emergent property of eight independent triggers.
"""

import logging
import os
import traceback
from datetime import datetime, timezone

from ops.activity import AgentRun, agent_enabled

logger = logging.getLogger(__name__)

AGENT = "daily_orchestrator"

# The pass must finish inside the Cloud Run request timeout with room for the
# seven agents that follow it. Cloud Run is deployed with --timeout=3600.
INGESTION_MAX_SECONDS = int(os.getenv("INGESTION_MAX_SECONDS", "1500"))


def _step(name: str, fn, report: dict, arun: AgentRun, *args, **kwargs) -> None:
    """Run one agent, record the outcome, never propagate."""
    if not agent_enabled(name):
        report[name] = {"skipped": "disabled via AGENT_DISABLED"}
        arun.info("step_disabled", f"{name} skipped (AGENT_DISABLED)")
        return
    t0 = datetime.now(timezone.utc)
    try:
        result = fn(*args, **kwargs)
        report[name] = result if isinstance(result, dict) else {"result": str(result)}
        elapsed = (datetime.now(timezone.utc) - t0).total_seconds()
        arun.success("step_ok", f"{name} finished in {elapsed:.0f}s")
    except Exception as exc:                      # noqa: BLE001
        report[name] = {"error": str(exc), "traceback": traceback.format_exc()[-2000:]}
        arun.error_("step_failed", f"{name} raised: {exc}")
        logger.exception("daily: step %s failed", name)


def _already_running(client, within_minutes: int = 60) -> str | None:
    """
    Is a daily pass already in flight? Returns its run id, or None.

    Cloud Scheduler stops waiting after its attempt deadline (1800s) but the
    Cloud Run request keeps going to --timeout=3600. A retry — or an impatient
    manual trigger — would therefore start a SECOND pass over the same queue
    rows: double the model spend, and two workers racing to publish the same
    draft. Scheduler retries are disabled, but this guard is what actually makes
    overlap impossible, since it also covers manual triggers.

    Bounded by `within_minutes` so a run row orphaned by a container crash
    (status stuck at 'running' forever) cannot wedge the pass permanently.
    Fails OPEN: if the check itself errors we proceed, because refusing to run
    on an unreadable table would turn a logging outage into an ingestion outage.
    """
    if client is None:
        return None
    from datetime import timedelta
    since = (datetime.now(timezone.utc) - timedelta(minutes=within_minutes)).isoformat()
    try:
        res = (client.table("agent_runs")
               .select("id,started_at")
               .eq("agent", AGENT)
               .eq("status", "running")
               .gte("started_at", since)
               .limit(1).execute())
        return res.data[0]["id"] if res.data else None
    except Exception as exc:                      # noqa: BLE001
        logger.debug("daily: overlap check unavailable (%s) — proceeding", exc)
        return None


def run(dry_run: bool = False, trigger: str = "scheduler",
        supabase_client=None) -> dict:
    """Run the full daily chain. Never raises."""
    started = datetime.now(timezone.utc)
    report: dict = {"started_at": started.isoformat(), "dry_run": dry_run, "steps": {}}
    steps = report["steps"]

    if not dry_run:
        try:
            from ops.activity import _client as _activity_client
            probe = supabase_client or _activity_client()
            in_flight = _already_running(probe)
        except Exception:                         # noqa: BLE001
            in_flight = None
        if in_flight:
            logger.warning("daily: pass %s is still running — skipping this trigger", in_flight)
            report["skipped"] = f"another daily pass ({in_flight}) is still in flight"
            report["summary"] = "skipped — overlapping pass"
            return report

    with AgentRun(AGENT, trigger=trigger) as arun:
        arun.stat("dry_run", dry_run)

        # ── 1. Ingestion ────────────────────────────────────────────────────
        def _ingest():
            from dataclasses import asdict
            from ingestion.orchestrator import run_ingestion_pass
            from ingestion.sources import get_enabled_sources
            rep = run_ingestion_pass(
                get_enabled_sources(),
                now=datetime.now(timezone.utc),
                dry_run=dry_run,
                max_duration_seconds=INGESTION_MAX_SECONDS,
                activity=arun,
            )
            d = asdict(rep)
            d["started_at"] = rep.started_at.isoformat()
            d["finished_at"] = rep.finished_at.isoformat()
            return d

        _step("ingestion", _ingest, steps, arun)

        # ── 2. Auto-publish + review notification ───────────────────────────
        def _auto():
            from ops.auto_publish import run as auto_run
            return auto_run(supabase_client=supabase_client, dry_run=dry_run, trigger="chained")

        _step("auto_publish", _auto, steps, arun)

        # ── 3-6. Monitoring fleet ───────────────────────────────────────────
        def _integrity():
            # apply=True in a real pass — req #10 asks for corrections, not just
            # detection. The agent's own auto-fix set is deliberately narrow
            # (recompute a drifted corroboration_count; dismiss an UNPROCESSED
            # queue duplicate). Anything touching a published incident's text,
            # dates or sources is report-and-email only, because picking which of
            # two duplicates is "the real one" is a judgement call and getting it
            # wrong rewrites live content with no human in the loop.
            from ops.integrity import run as integrity_run
            return integrity_run(supabase_client=supabase_client,
                                 apply=not dry_run, trigger="chained")

        def _supervisor():
            from ops.supervisor import run as supervisor_run
            return supervisor_run(supabase_client=supabase_client, trigger="chained")

        def _learning():
            from ops.learning_monitor import run as learning_run
            return learning_run(supabase_client=supabase_client, trigger="chained")

        def _health():
            from ops.backend_health import run as health_run
            return health_run(supabase_client=supabase_client, trigger="chained")

        _step("integrity", _integrity, steps, arun)
        _step("supervisor", _supervisor, steps, arun)
        _step("learning_monitor", _learning, steps, arun)
        _step("backend_health", _health, steps, arun)

        # ── 7. Maintenance digest — last, so it sees the whole day ──────────
        def _maintenance():
            from ops.maintenance import run as maintenance_run
            return maintenance_run(supabase_client=supabase_client, trigger="chained")

        _step("maintenance", _maintenance, steps, arun)

        # ── 8. Monthly report — 1st of the month only ──────────────────────
        today = datetime.now(timezone.utc).date()
        if today.day == 1 or os.getenv("FORCE_MONTHLY_REPORT", "").lower() == "true":
            def _monthly():
                from ops.monthly_report import run as monthly_run
                return monthly_run(supabase_client=supabase_client, trigger="chained")
            _step("monthly_report", _monthly, steps, arun)
        else:
            steps["monthly_report"] = {"skipped": f"not the 1st (today is the {today.day})"}

        # ── Summary ─────────────────────────────────────────────────────────
        failed = [k for k, v in steps.items() if isinstance(v, dict) and v.get("error")]
        queued = (steps.get("ingestion") or {}).get("total_queued", 0)
        published = (steps.get("auto_publish") or {}).get("published", 0)
        review = (steps.get("auto_publish") or {}).get("needs_review", 0)

        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        report["duration_seconds"] = round(
            (datetime.now(timezone.utc) - started).total_seconds(), 1)
        report["failed_steps"] = failed

        summary = (f"queued {queued}, auto-published {published}, {review} for review"
                   f"{'; FAILED: ' + ', '.join(failed) if failed else ''}")
        arun.set_summary(summary)
        arun.stat("queued", queued)
        arun.stat("auto_published", published)
        arun.stat("needs_review", review)
        arun.stat("failed_steps", failed)
        if failed:
            arun.set_status("degraded")
        report["summary"] = summary
        logger.info("daily orchestrator: %s (%.0fs)", summary, report["duration_seconds"])

    return report
