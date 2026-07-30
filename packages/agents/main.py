import hmac
import logging
import os
from contextlib import asynccontextmanager

import uvicorn
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import PlainTextResponse

load_dotenv(override=False)


def _require_ops_token(x_ops_token: str = Header(..., alias="X-Ops-Token")) -> None:
    """Shared-secret gate for all ops endpoints (B2 security fix).

    Set OPS_TOKEN in the Cloud Run environment. Every caller (Cloud Scheduler,
    manual curl) must pass X-Ops-Token: <token> or gets 401. If OPS_TOKEN is
    not configured on the server the endpoint returns 503 rather than silently
    allowing unauthenticated access.
    """
    # .strip() both sides: a secret created on Windows can carry a trailing \r
    # that never survives the HTTP hop, so the values differ by one invisible
    # byte and every request 401s with nothing in the logs to explain it. This
    # cost an hour once; it does not weaken the comparison.
    token = os.getenv("OPS_TOKEN", "").strip()
    if not token:
        raise HTTPException(status_code=503, detail="OPS_TOKEN not configured on server")
    if not hmac.compare_digest(x_ops_token.strip(), token):
        raise HTTPException(status_code=401, detail="Invalid X-Ops-Token")


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("yishun-agents")


# ---------------------------------------------------------------------------
# Scheduler job wrappers
# ---------------------------------------------------------------------------

def _job_pipeline() -> None:
    """Full daily chain — ingestion, auto-publish, then the monitoring fleet."""
    from ops.daily import run as daily_run
    try:
        report = daily_run(trigger="scheduler")
        logger.info("Daily orchestrator: %s", report.get("summary"))
    except Exception as exc:
        logger.error("Pipeline job failed: %s", exc)


# The nine per-source "health check" jobs that used to live here are gone. They
# re-scraped each site on its own interval purely to log a count, duplicating
# work the ingestion pass already does — and every one of them needed the
# in-process scheduler to be running. Source health now comes from the real
# pass, via pipeline_state and ops/supervisor.py.
#
# The pattern-detection, recalibration, lifecycle and source-discovery jobs are
# gone from here for a sharper reason: they were only ever registered on this
# scheduler, which production does not start, so in production they had never
# run at all. They are now cadence-gated steps inside ops/daily.py, on the same
# schedules, reached by the one entry point Cloud Scheduler actually calls. Two
# places defining one schedule is what let them drift into being dead code; there
# is now exactly one.


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------
# PRODUCTION USES CLOUD SCHEDULER, NOT THIS.
#
# The in-process APScheduler below is OFF by default and exists for local
# development only. On Cloud Run it is the wrong tool twice over:
#
#   * Correctness — the service scales to zero. A background scheduler inside a
#     container that is not running does not fire. Making it fire requires
#     min-instances=1 AND CPU-always-allocated.
#   * Cost (req #12) — that combination is roughly $15-25/month to execute
#     ~15 minutes of daily work. One Cloud Scheduler ping to
#     POST /orchestrator/daily costs nothing (3 jobs are free) and lets the
#     service sit at zero instances the other 23h 45m.
#
# Set ENABLE_INPROCESS_SCHEDULER=true to run the old interval jobs locally.
# The daily chain, its ordering, and its failure isolation live in ops/daily.py.

def _scheduler_enabled() -> bool:
    return os.getenv("ENABLE_INPROCESS_SCHEDULER", "false").strip().lower() in ("true", "1", "yes")


# ONE job. The chain it triggers carries every cadence — daily, weekly and
# monthly — inside ops/daily.py, so this list stays a single entry no matter how
# many agents the fleet grows.
_JOBS = [
    # Full daily chain — ingestion → auto-publish → monitoring fleet → the
    # cadence-gated agents. 14:58 SGT, matching the Cloud Scheduler cron so
    # local and prod agree.
    (
        _job_pipeline,
        CronTrigger(hour=14, minute=58, timezone="Asia/Singapore"),
        "daily_orchestrator",
    ),
]


def _build_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="Asia/Singapore")
    for fn, trigger, job_id in _JOBS:
        scheduler.add_job(
            fn, trigger, id=job_id,
            replace_existing=True,
            misfire_grace_time=300,
        )
        logger.info("Scheduled job: %s (%s)", job_id, trigger)
    return scheduler


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = None
    if _scheduler_enabled():
        scheduler = _build_scheduler()
        scheduler.start()
        logger.info("yishun-agents starting up — %d in-process job(s) scheduled", len(_JOBS))
    else:
        logger.info(
            "yishun-agents starting up — in-process scheduler DISABLED "
            "(production is driven by Cloud Scheduler -> POST /orchestrator/daily). "
            "Set ENABLE_INPROCESS_SCHEDULER=true for local scheduling."
        )
    yield
    if scheduler:
        scheduler.shutdown(wait=False)
    logger.info("yishun-agents shutting down")


app = FastAPI(
    title="Yishun Again — Agents",
    description="AI agent pipeline for the Yishun Again incident archive.",
    version="0.2.0",
    lifespan=lifespan,
    # No interactive docs: these were the only endpoints besides /health with
    # no app-level auth, and they enumerate the full API surface if the
    # service is ever deployed --allow-unauthenticated by mistake.
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "yishun-agents"}


@app.post("/pipeline/run", tags=["ops"])
async def trigger_pipeline(
    dry_run: bool = Query(False, description="Run without writing to Supabase"),
    _: None = Depends(_require_ops_token),
):
    """
    Manually trigger one ingestion pass (ingestion/orchestrator.run_ingestion_pass).
    Set dry_run=true to see what would be queued without inserting anything.
    """
    import asyncio
    from dataclasses import asdict
    from datetime import datetime, timezone
    from ingestion.orchestrator import run_ingestion_pass
    from ingestion.sources import get_enabled_sources

    def _run():
        return run_ingestion_pass(
            get_enabled_sources(), now=datetime.now(timezone.utc), dry_run=dry_run,
        )

    loop = asyncio.get_event_loop()
    report = await loop.run_in_executor(None, _run)
    result = asdict(report)
    result["started_at"] = report.started_at.isoformat()
    result["finished_at"] = report.finished_at.isoformat()
    return result


@app.post("/orchestrator/daily", tags=["ops"])
async def trigger_daily(
    dry_run: bool = Query(False, description="Run the whole chain without writing"),
    _: None = Depends(_require_ops_token),
):
    """
    THE production entry point. Cloud Scheduler POSTs here once a day at 14:58 SGT.

    Runs ingestion -> auto-publish -> integrity -> supervisor -> learning ->
    health -> pattern detection -> recalibration -> lifecycle (Mondays) ->
    source discovery (first Monday) -> maintenance (-> monthly report on the
    1st). Individual agent failures are isolated and reported; see ops/daily.py
    for the ordering rationale and the cadence gates.

    dry_run skips every cadence-gated step outright — none of them has a
    read-only mode, so "run the chain writing nothing" cannot include them.

    Long-running by design (~5-20 min). The Cloud Run service is deployed with
    --timeout=3600 to accommodate it.
    """
    import asyncio
    from ops.daily import run as daily_run

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: daily_run(dry_run=dry_run, trigger="scheduler"))


@app.get("/agents/status", tags=["ops"])
async def agents_status(
    hours: int = Query(24, ge=1, le=720),
    _: None = Depends(_require_ops_token),
):
    """Recent agent runs plus any error/anomaly events — the fleet at a glance."""
    import asyncio
    from ops.activity import recent_events, recent_runs, stale_runs

    loop = asyncio.get_event_loop()

    def _gather():
        return {
            "runs": recent_runs(hours=hours),
            "events": recent_events(hours=hours),
            "stale_runs": stale_runs(),
        }

    return await loop.run_in_executor(None, _gather)


@app.post("/notify/test", tags=["ops"])
async def trigger_notify_test(_: None = Depends(_require_ops_token)):
    """
    Send one test email to OPERATOR_EMAIL. Use after a deploy to prove the
    alerting path works — an alerting system nobody has ever seen deliver a
    message is not an alerting system.
    """
    import asyncio
    from datetime import datetime, timezone

    from ops.notify import footer, notify

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: notify(
        "test",
        "Yishun Again — alerting test",
        "If you are reading this, the agent fleet can reach you." + footer(),
        dedup_key=f"test:{datetime.now(timezone.utc).isoformat()}",
    ))


@app.post("/pattern/run", tags=["ops"])
async def trigger_pattern_detection(_: None = Depends(_require_ops_token)):
    """Manually trigger a full pattern detection sweep."""
    import asyncio
    from classifiers.pattern_detection import run as pattern_run

    loop = asyncio.get_event_loop()
    stats = await loop.run_in_executor(None, pattern_run)
    return stats


@app.post("/recalibration/run", tags=["ops"])
async def trigger_recalibration(_: None = Depends(_require_ops_token)):
    """Manually trigger a recalibration check against all training signals."""
    import asyncio
    from classifiers.recalibration import check as recal_check

    loop = asyncio.get_event_loop()
    stats = await loop.run_in_executor(None, recal_check)
    return stats


@app.post("/lifecycle/run", tags=["ops"])
async def trigger_lifecycle(_: None = Depends(_require_ops_token)):
    """
    Manually trigger the lifecycle timeout check.

    Equivalent to the Monday step in the daily chain, and safe to run at any
    time. Note this endpoint does NOT consult LIFECYCLE_AUTO_CONCLUDE: that flag
    gates the unattended run, and calling this by hand IS the operator decision
    the flag exists to require. Auto-conclusion edits published incidents.
    """
    import asyncio
    from classifiers.lifecycle import run as lifecycle_run

    loop = asyncio.get_event_loop()
    stats = await loop.run_in_executor(None, lifecycle_run)
    return stats


@app.post("/discovery/run", tags=["ops"])
async def trigger_discovery(_: None = Depends(_require_ops_token)):
    """
    Manually trigger source discovery.

    Files novel outlets seen in Google News as `sources` rows that are neither
    approved nor active, for review in War Room. Nothing is scraped or cited
    until the operator approves the domain.
    """
    import asyncio
    from scrapers.scrape_discovery import run as discovery_run

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, discovery_run)


@app.post("/backfill/run", tags=["ops"])
async def trigger_backfill(_: None = Depends(_require_ops_token)):
    """
    DEPRECATED (INGESTION_DESIGN.md §10b). The historical backfill agent
    (scrapers.backfill_agent.run_backfill) is retired — historical incidents
    have already been backfilled. The live forward pipeline is
    run_ingestion_pass(), triggered via /pipeline/run or the scheduler.
    """
    return {
        "deprecated": True,
        "detail": "Backfill is retired. Use /pipeline/run for the forward ingestion pipeline.",
    }


@app.post("/geocoding/backfill", tags=["ops"])
async def trigger_geocoding_backfill(_: None = Depends(_require_ops_token)):
    """
    Find all published incidents with NULL coordinates but a block_number or area_name,
    geocode each via the Singapore OneMap API, and update Supabase.
    Returns {"updated": N, "failed": N}.
    Safe to run multiple times — skips incidents that already have coordinates.
    """
    import asyncio
    from classifiers.geocoding import backfill_missing_coordinates

    loop = asyncio.get_event_loop()
    stats = await loop.run_in_executor(None, backfill_missing_coordinates)
    return stats


@app.get("/autonomy/status", tags=["ops"])
async def autonomy_status(_: None = Depends(_require_ops_token)):
    """Per-category autonomy calibration data derived from training_signals."""
    import asyncio
    from classifiers.autonomy_tracker import get_autonomy_status

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, get_autonomy_status)


@app.get("/autonomy/report", tags=["ops"], response_class=PlainTextResponse)
async def autonomy_report(_: None = Depends(_require_ops_token)):
    """Human-readable autonomy graduation report."""
    import asyncio
    from classifiers.autonomy_tracker import get_graduation_report

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, get_graduation_report)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
