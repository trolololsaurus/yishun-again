import hmac
import logging
import os
from contextlib import asynccontextmanager

import uvicorn
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
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
    token = os.getenv("OPS_TOKEN", "")
    if not token:
        raise HTTPException(status_code=503, detail="OPS_TOKEN not configured on server")
    if not hmac.compare_digest(x_ops_token, token):
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
    """Full ingestion pass — fetch -> Stage 1/2 -> consolidation -> war_room_queue."""
    from datetime import datetime, timezone
    from ingestion.orchestrator import run_ingestion_pass
    from ingestion.sources import get_enabled_sources
    try:
        report = run_ingestion_pass(
            get_enabled_sources(), now=datetime.now(timezone.utc), dry_run=False,
        )
        logger.info(
            "Ingestion pass: total_queued=%d new=%d update=%d degraded=%s infra_error=%s",
            report.total_queued, report.new_count, report.update_count,
            report.degraded, report.infra_error,
        )
    except Exception as exc:
        logger.error("Pipeline job failed: %s", exc)


# Individual per-source health-check jobs — scrape and log only, no AI processing.
# Full pipeline (Stage 1 → Stage 2 → queue) runs every 30 min via _job_pipeline.

def _scrape_job(module: str, label: str) -> None:
    """Generic wrapper: import scrape(), run it, log count."""
    try:
        import importlib
        mod = importlib.import_module(f"scrapers.{module}")
        items = mod.scrape()
        logger.info("Scraper [%s]: %d item(s)", label, len(items))
    except Exception as exc:
        logger.error("Scraper [%s] failed: %s", label, exc)


def _job_straitstimes()    -> None: _scrape_job("scrape_straitstimes",   "Straits Times")
def _job_mustsharenews()   -> None: _scrape_job("scrape_mustsharenews",  "MustShareNews")
def _job_theindependent()  -> None: _scrape_job("scrape_theindependent", "The Independent")
def _job_jom()             -> None: _scrape_job("scrape_jom",            "Jom")
def _job_yahoo()           -> None: _scrape_job("scrape_yahoo",          "Yahoo")
def _job_asiaone()         -> None: _scrape_job("scrape_asiaone",        "AsiaOne")
def _job_zaobao()          -> None: _scrape_job("scrape_zaobao",         "Zaobao")
def _job_shinmin()         -> None: _scrape_job("scrape_shinmin",        "Shinmin")
def _job_beritaharian()    -> None: _scrape_job("scrape_beritaharian",   "Berita Harian")
def _job_tamilmurasu()     -> None: _scrape_job("scrape_tamilmurasu",    "Tamil Murasu")


def _job_pattern_detection() -> None:
    """Daily pattern detection + recalibration check."""
    from classifiers.pattern_detection import run as pattern_run
    from classifiers.recalibration     import check as recal_check
    try:
        stats = pattern_run()
        logger.info(
            "Pattern detection job: alerts_created=%d patterns_found=%d "
            "entities_checked=%d errors=%d",
            stats["alerts_created"], stats["patterns_found"],
            stats["entities_checked"], stats["errors"],
        )
    except Exception as exc:
        logger.error("Pattern detection job failed: %s", exc)

    try:
        recal = recal_check()
        if recal["recalibrated"]:
            logger.info(
                "Recalibration: updated signal types: %s",
                recal["signal_types_updated"],
            )
    except Exception as exc:
        logger.warning("Recalibration check failed (non-fatal): %s", exc)


def _job_lifecycle() -> None:
    """Weekly timeout check — auto-concludes developing stories with no updates in 180 days."""
    from classifiers.lifecycle import run as lifecycle_run
    try:
        stats = lifecycle_run()
        logger.info(
            "Lifecycle job: concluded=%d errors=%d",
            stats["concluded"], stats["errors"],
        )
    except Exception as exc:
        logger.error("Lifecycle job failed: %s", exc)


def _job_discovery() -> None:
    """Monthly source discovery — flags new outlets for operator review."""
    from scrapers.scrape_discovery import discover
    from classifiers.corroboration import get_supabase_client
    try:
        candidates = discover()
        logger.info(
            "Source discovery: %d candidate(s) found", len(candidates),
        )
        if not candidates:
            return
        # Write candidates to sources table (approved_by_operator = FALSE).
        # Operator reviews them in War Room → Sources tab.
        client = get_supabase_client()
        for c in candidates:
            try:
                client.table("sources").insert({
                    "name":                 c["name"],
                    "url":                  c["url"],
                    "type":                 c.get("type", "msm"),
                    "approved_by_operator": False,
                    "discovery_notes":      c.get("notes", ""),
                    "scrape_interval_minutes": 60,
                }).execute()
                logger.info("  Candidate inserted: %s", c["name"])
            except Exception as exc:
                # Duplicate name — already known, skip silently.
                logger.debug("  Candidate skip (%s): %s", c["name"], exc)
    except Exception as exc:
        logger.error("Source discovery job failed: %s", exc)


# ---------------------------------------------------------------------------
# APScheduler setup
# ---------------------------------------------------------------------------
# Pipeline runs every 30 min and calls all scrapers internally.
# Individual per-source intervals are handled inside pipeline/scrape_all().
# Discovery runs once a month (first Monday, 09:00 SGT).

_JOBS = [
    # Full pipeline — all scrapers → Stage 1 → Stage 2 → war_room_queue
    (_job_pipeline,       IntervalTrigger(minutes=30),  "pipeline"),
    # Individual health-check jobs at spec intervals (scrape + log only)
    (_job_straitstimes,   IntervalTrigger(minutes=60),  "straitstimes"),
    (_job_mustsharenews,  IntervalTrigger(minutes=60),  "mustsharenews"),
    (_job_theindependent, IntervalTrigger(minutes=60),  "theindependent"),
    (_job_jom,            IntervalTrigger(minutes=360), "jom"),
    (_job_yahoo,          IntervalTrigger(minutes=120), "yahoo"),
    (_job_asiaone,        IntervalTrigger(minutes=120), "asiaone"),
    (_job_zaobao,         IntervalTrigger(minutes=180), "zaobao"),
    (_job_shinmin,        IntervalTrigger(minutes=180), "shinmin"),
    (_job_beritaharian,   IntervalTrigger(minutes=180), "beritaharian"),
    (_job_tamilmurasu,    IntervalTrigger(minutes=180), "tamilmurasu"),
    # Daily pattern detection — 06:00 SGT
    (
        _job_pattern_detection,
        CronTrigger(hour=6, minute=0, timezone="Asia/Singapore"),
        "pattern_detection",
    ),
    # Weekly lifecycle timeout — auto-concludes developing stories after 180 days
    (
        _job_lifecycle,
        CronTrigger(day_of_week="mon", hour=0, minute=0, timezone="Asia/Singapore"),
        "lifecycle_timeout",
    ),
    # Monthly source discovery
    (
        _job_discovery,
        CronTrigger(day="1-7", day_of_week="mon", hour=9, minute=0,
                    timezone="Asia/Singapore"),
        "source_discovery",
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
    scheduler = _build_scheduler()
    scheduler.start()
    logger.info("yishun-agents starting up — %d job(s) scheduled", len(_JOBS))
    yield
    scheduler.shutdown(wait=False)
    logger.info("yishun-agents shutting down")


app = FastAPI(
    title="Yishun Again — Agents",
    description="AI agent pipeline for the Yishun Again incident archive.",
    version="0.2.0",
    lifespan=lifespan,
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
    Equivalent to the weekly Monday cron — safe to run at any time.
    """
    import asyncio
    from classifiers.lifecycle import run as lifecycle_run

    loop = asyncio.get_event_loop()
    stats = await loop.run_in_executor(None, lifecycle_run)
    return stats


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
