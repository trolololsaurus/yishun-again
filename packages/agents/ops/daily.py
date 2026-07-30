"""
The daily orchestrator (req #13) — one entry point the scheduler calls at 14:58 SGT.

Runs the whole fleet in a deliberate order and returns a single report.

## Order is not arbitrary

     1. recalibration    operator corrections -> calibration_log.json. FIRST, and
                         the reason is subtle enough to be worth a paragraph below
     2. ingestion        produce candidate drafts
     3. auto_publish     publish >= threshold; email about everything below (#3, #4)
     4. integrity        dupes + hallucinations — AFTER publish, so it audits what
                         actually went live, not what was merely proposed (#10)
     5. supervisor       scraper fleet health — AFTER ingestion, so it grades this
                         pass rather than yesterday's (#9)
     6. learning_monitor rebuild source_reputation, snapshot the deltas (#5)
     7. backend_health   Supabase / R2 / API / cost guard (#12)
     8. pattern_detection  entity / crime-type / location alerts — AFTER publish,
                         so today's incidents are in the pool it scans
     9. lifecycle        Mondays: auto-conclude developing stories idle 180 days
    10. source_discovery first Monday: novel outlets -> sources, unapproved
    11. maintenance      reads everything above and mails ONE digest — so it must
                         run LAST or it reports stale news (#11)
    12. monthly_report   1st of the month only; last 30 days (#13)

Steps 1, 8, 9, 10 and 12 are CADENCE-GATED (see `cadence_plan`). Until
2026-07-30 the first four of those were scheduled only on the in-process
APScheduler in main.py — which production never starts, because Cloud Run scales
to zero. They had therefore never run at all. The schedule now lives here and
only here, so local and prod cannot drift apart again.

## Why recalibration runs BEFORE ingestion

It looks like a monitoring agent and every instinct says to group it with the
other five. But `classifiers/recalibration.py` writes `calibration_log.json`,
and `filters/stage2_writer._load_calibration_hints` READS that file while
drafting — so the two are producer and consumer inside one pass, not independent
observers.

That file lives on Cloud Run's ephemeral disk (AUTONOMY.md §6, "known gap"), and
with min-instances=0 the container is replaced between passes. So a run of
recalibration placed after ingestion writes hints that are destroyed before any
Stage 2 call ever opens them: the loop would look wired and stay a no-op, which
is the same shape of bug as scheduling it on a scheduler that never starts.
Running it first makes the hints land in the container that is about to draft.

Move it back down the list and the calibration loop silently stops working.
Persisting the hints to Supabase would remove the constraint; until then, this
ordering is the whole mechanism.

## Nothing cadence-gated runs on a dry run

`?dry_run=true` is the "show me what the chain would do, writing nothing" lever.
Ingestion, auto-publish and integrity honour it. None of the five cadence-gated
agents takes a dry_run argument: called at all, they conclude incidents, insert
pattern alerts, write source rows and upsert a monthly report for real. Rather
than thread an untested dry_run through five modules to serve one debugging
flag, a dry run skips them outright and says so in the report.

## Failure isolation

Every step is wrapped. A crash in step 4 must not cost the operator steps 5-12 —
the monitoring agents are most valuable precisely when something has gone wrong.
A failed step is recorded in the report with its traceback and the chain
continues. The only genuinely fatal condition is being unable to reach Supabase
at all, which every step reports independently anyway.

## Why this exists instead of twelve scheduler entries

Cloud Run scales to zero. In-process APScheduler needs min-instances=1 and
CPU-always-allocated to fire reliably — about $15-25/month to run jobs that
occupy ~15 minutes of CPU a day. One Cloud Scheduler ping to one endpoint keeps
the service at zero instances the other 23h 45m, which is the whole of req #12's
cost answer. It also makes the ordering above explicit and testable, rather than
an emergent property of twelve independent triggers.
"""

import logging
import os
import traceback
from datetime import date, datetime, timedelta, timezone

from ops.activity import AgentRun, agent_enabled

logger = logging.getLogger(__name__)

AGENT = "daily_orchestrator"

# The pass must finish inside the Cloud Run request timeout with room for the
# agents that follow it. Cloud Run is deployed with --timeout=3600.
INGESTION_MAX_SECONDS = int(os.getenv("INGESTION_MAX_SECONDS", "1500"))

# Singapore is UTC+8 all year and has never observed DST, so a fixed offset is
# exact. Deliberately not zoneinfo: that needs the tzdata database present in
# the image, and a missing tzdata would turn "is it Monday?" into an exception
# inside the scheduler's own entry point.
SGT = timezone(timedelta(hours=8))


def _flag(name: str) -> bool:
    """An env-var switch, default off. Anything unrecognised stays off."""
    return os.getenv(name, "").strip().lower() in ("true", "1", "yes", "on")


def sgt_today(now: datetime | None = None) -> date:
    """
    Today's date in Singapore time.

    The whole schedule is written and discussed in SGT ("14:58 SGT", "Mondays"),
    so the cadence gates must agree with the operator's calendar. For the 14:58
    SGT trigger the UTC date happens to match, but a manual 02:00 SGT trigger is
    still the previous day in UTC — which would fire Monday's lifecycle run on a
    Sunday.
    """
    return (now or datetime.now(timezone.utc)).astimezone(SGT).date()


def is_first_monday(day: date) -> bool:
    """First Monday of the month — the old `day='1-7', day_of_week='mon'` cron."""
    return day.weekday() == 0 and day.day <= 7


# In execution order. Every one writes to Supabase (or, for recalibration, to
# disk) the moment it is called — none has a read-only mode, which is why a dry
# run must not call them at all.
CADENCE_STEPS = ("recalibration", "pattern_detection", "lifecycle",
                 "source_discovery", "monthly_report")

_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday",
             "Friday", "Saturday", "Sunday")


def cadence_plan(today: date, *, dry_run: bool = False,
                 lifecycle_enabled: bool | None = None,
                 force_monthly: bool | None = None) -> dict[str, str | None]:
    """
    Which cadence-gated steps are due today. Pure — this is the part worth testing.

    Returns {step_name: None if due, else the reason it was skipped}. The
    cadences mirror the cron expressions the in-process scheduler used to carry,
    so anyone who was running it locally sees no change.
    """
    if lifecycle_enabled is None:
        lifecycle_enabled = _flag("LIFECYCLE_AUTO_CONCLUDE")
    if force_monthly is None:
        force_monthly = _flag("FORCE_MONTHLY_REPORT")

    weekday = _WEEKDAYS[today.weekday()]
    plan: dict[str, str | None] = {}

    # Daily.
    plan["pattern_detection"] = None
    plan["recalibration"] = None

    # Weekly, Mondays — and only with the operator's switch on. The switch is
    # checked first so that on a Monday with autonomy off the report says the
    # useful thing ("it is disabled") rather than nothing at all.
    if not lifecycle_enabled:
        plan["lifecycle"] = ("LIFECYCLE_AUTO_CONCLUDE is off — auto-conclude edits "
                             "published incidents, so it stays opt-in (AUTONOMY.md §5d)")
    elif today.weekday() != 0:
        plan["lifecycle"] = f"not Monday (today is {weekday})"
    else:
        plan["lifecycle"] = None

    # Monthly, first Monday.
    plan["source_discovery"] = (
        None if is_first_monday(today)
        else f"not the first Monday (today is {weekday} the {today.day})"
    )

    # Monthly, the 1st.
    plan["monthly_report"] = (
        None if today.day == 1 or force_monthly
        else f"not the 1st (today is the {today.day})"
    )

    if dry_run:
        for step in CADENCE_STEPS:
            plan[step] = "dry run — this agent has no read-only mode"

    return plan


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


def _cadence_step(name: str, fn, report: dict, arun: AgentRun,
                  not_due: str | None) -> None:
    """A `_step` that only fires on its scheduled day. Same failure isolation."""
    if not_due:
        report[name] = {"skipped": not_due}
        return
    _step(name, fn, report, arun)


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

        today = sgt_today()
        plan = cadence_plan(today, dry_run=dry_run)
        report["cadence_date_sgt"] = today.isoformat()

        # ── 1. Recalibration — BEFORE ingestion, on purpose ──────────────────
        # It writes the calibration hints that this pass's Stage 2 is about to
        # read, and they live on a disk that does not survive to the next pass.
        # See "Why recalibration runs BEFORE ingestion" in the module docstring
        # before moving this.
        def _recalibration():
            from classifiers.recalibration import check as recal_check
            return recal_check(supabase_client=supabase_client)

        _cadence_step("recalibration", _recalibration, steps, arun, plan["recalibration"])

        # ── 2. Ingestion ────────────────────────────────────────────────────
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

        # ── 3. Auto-publish + review notification ───────────────────────────
        def _auto():
            from ops.auto_publish import run as auto_run
            return auto_run(supabase_client=supabase_client, dry_run=dry_run, trigger="chained")

        _step("auto_publish", _auto, steps, arun)

        # ── 4-7. Monitoring fleet ───────────────────────────────────────────
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

        # ── 8-10. Cadence-gated editorial agents ────────────────────────────
        # Wired in 2026-07-30. Along with recalibration above, these were
        # scheduled only on the in-process APScheduler, which production never
        # starts, so none of them had ever run: no auto-conclusions, no pattern
        # alerts, and Stage 2 read calibration hints nothing had written.
        def _pattern():
            from classifiers.pattern_detection import run as pattern_run
            return pattern_run(supabase_client=supabase_client)

        def _lifecycle():
            from classifiers.lifecycle import run as lifecycle_run
            return lifecycle_run(supabase_client=supabase_client)

        def _discovery():
            from scrapers.scrape_discovery import run as discovery_run
            return discovery_run(supabase_client=supabase_client)

        _cadence_step("pattern_detection", _pattern, steps, arun, plan["pattern_detection"])
        _cadence_step("lifecycle", _lifecycle, steps, arun, plan["lifecycle"])
        _cadence_step("source_discovery", _discovery, steps, arun, plan["source_discovery"])

        # ── 11. Maintenance digest — last, so it sees the whole day ─────────
        def _maintenance():
            from ops.maintenance import run as maintenance_run
            return maintenance_run(supabase_client=supabase_client, trigger="chained")

        _step("maintenance", _maintenance, steps, arun)

        # ── 12. Monthly report — 1st of the month only ─────────────────────
        def _monthly():
            from ops.monthly_report import run as monthly_run
            return monthly_run(supabase_client=supabase_client, trigger="chained")

        _cadence_step("monthly_report", _monthly, steps, arun, plan["monthly_report"])

        # ── Summary ─────────────────────────────────────────────────────────
        failed = [k for k, v in steps.items() if isinstance(v, dict) and v.get("error")]
        queued = (steps.get("ingestion") or {}).get("total_queued", 0)
        published = (steps.get("auto_publish") or {}).get("published", 0)
        review = (steps.get("auto_publish") or {}).get("needs_review", 0)

        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        report["duration_seconds"] = round(
            (datetime.now(timezone.utc) - started).total_seconds(), 1)
        report["failed_steps"] = failed

        alerts = (steps.get("pattern_detection") or {}).get("alerts_created", 0)
        concluded = (steps.get("lifecycle") or {}).get("concluded", 0)
        extra = ", ".join(
            part for part in (
                f"{alerts} pattern alert(s)" if alerts else "",
                f"{concluded} auto-concluded" if concluded else "",
            ) if part
        )

        summary = (f"queued {queued}, auto-published {published}, {review} for review"
                   f"{'; ' + extra if extra else ''}"
                   f"{'; FAILED: ' + ', '.join(failed) if failed else ''}")
        arun.set_summary(summary)
        arun.stat("queued", queued)
        arun.stat("auto_published", published)
        arun.stat("needs_review", review)
        arun.stat("failed_steps", failed)
        # Which cadence gates fired and, more usefully, why the others did not —
        # otherwise "lifecycle did not run last night" has no answer in the DB.
        arun.stat("cadence", plan)
        if failed:
            arun.set_status("degraded")
        report["summary"] = summary
        logger.info("daily orchestrator: %s (%.0fs)", summary, report["duration_seconds"])

    return report
