"""
Scraper supervisor (req #9) — watch the live sources, mail the operator only
when something is genuinely broken.

The pipeline already records everything this agent needs: `pipeline_state`
(per-source status + failure streak), `scraper_health` (items found, zero
streaks) and `agent_runs`/`agent_events` (what the fleet actually did). Nothing
here scrapes or probes — it reads the record and decides whether a human has to
hear about it tonight.

WHY THE EMAIL BAR IS DELIBERATELY HIGH
--------------------------------------
An alerting system is only worth building if the operator still opens it in
month three. A single flaky source is normal: sites change markup, Cloudflare
has moods, a feed 404s for an afternoon and comes back. Mailing on every one of
those teaches the operator to filter the sender — and then the ONE alert that
mattered is invisible too. So a lone broken source is LOGGED (visible in War
Room) and not mailed.

Email is reserved for the four shapes that mean the archive has stopped
updating and will not fix itself:

  (a) >= 3 sources anomalous in one pass — too many to be coincidence; that is
      network, credentials or a bad deploy, not a site redesign.
  (b) EVERY source failing — the unambiguous version of (a).
  (c) one source anomalous on >= 3 consecutive days — no longer "flaky", it is
      broken and nobody noticed.
  (d) an agent run stuck in 'running' — the container died mid-pass, so no
      later pass will fire on its own and no other rule here can save us.

Everything else is a warning in the activity log.

Public API
----------
run(supabase_client=None, trigger="scheduler") -> dict
    {"sources_checked", "warnings", "anomalies", "serious", "emailed", "errors"}
"""

import logging
from datetime import datetime, timedelta, timezone

from ops.activity import AgentRun, agent_enabled, recent_events, stale_runs
from ops.notify import footer, notify, war_room_url

logger = logging.getLogger(__name__)

AGENT = "supervisor"

# ── Thresholds ───────────────────────────────────────────────────────────────
# A source that fails once is noise; three passes in a row is a pattern.
FAILURE_STREAK_ANOMALY = 3

# The pass runs daily, so 48h means two whole passes produced nothing. One
# missed pass is a Cloud Run hiccup; two is a stopped scheduler.
STALE_HOURS = 48

# Yishun-specific filtering legitimately returns 0 items on most days — zero
# items is the NORMAL case, not a failure. Only a long unbroken streak means the
# scraper stopped seeing the page it used to parse.
ZERO_STREAK_ANOMALY = 5

# An ingestion pass is minutes, not hours. Still 'running' after 90 minutes
# means the row was never closed — i.e. the process died.
STUCK_RUN_MINUTES = 90

# scraper_health rows are evidence only while they are FRESH. This agent spent
# months grading rows nobody was writing any more: the table's writer lived in
# `scrapers.scrape_all`, which lost its last caller when ingestion moved to the
# source adapters, so every pass re-read the same fossils and reported them as
# today's fleet. `ingestion/health.py` writes them on the live path now — and
# this cutoff means a future regression of the same shape degrades to "no
# opinion" instead of a confident wrong one.
HEALTH_ROW_MAX_AGE_HOURS = 48

# (a) and (c) from the module docstring.
SERIOUS_SOURCE_COUNT = 3
CHRONIC_DAYS = 3

# "Every source is failing" only carries information when there are enough
# sources for the word "every" to mean something. On a one- or two-source
# registration (a dev box, or a half-migrated fleet) it is just the per-source
# finding restated in a louder voice — and it would mail on every hiccup.
MIN_FLEET_FOR_FLEETWIDE = 3


def _client(explicit=None):
    """Return a Supabase client, or None. Never raises."""
    if explicit is not None:
        return explicit
    try:
        from classifiers.corroboration import get_supabase_client
        return get_supabase_client()
    except Exception as exc:                      # noqa: BLE001 - see module docstring
        logger.warning("supervisor: no Supabase client (%s) — nothing to supervise", exc)
        return None


def _parse_ts(value):
    """Postgres timestamptz -> aware datetime. None on anything unparseable."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# ── Reads ────────────────────────────────────────────────────────────────────

def load_pipeline_state(client=None) -> list[dict]:
    """One row per registered source. Returns [] on any failure."""
    c = _client(client)
    if not c:
        return []
    try:
        res = (c.table("pipeline_state")
               .select("source_name,last_run_at,watermark,last_status,last_reason,"
                       "consecutive_failures,updated_at")
               .execute())
        return res.data or []
    except Exception as exc:                      # noqa: BLE001
        logger.warning("supervisor: pipeline_state read failed: %s", exc)
        return []


def load_scraper_health(client=None, limit: int = 300) -> list[dict]:
    """
    Latest scraper_health row per source.

    scraper_health is append-only, so the table is read newest-first and the
    first row seen for a source wins. `consecutive_zeros` is already cumulative
    on each row (`ingestion.health.record` computes it), so one row per source
    is all the history this agent needs.

    Rows are NOT filtered by age here — classify_findings applies
    HEALTH_ROW_MAX_AGE_HOURS against its own `now`, which keeps the age rule
    testable and lets it tell "stale rows" apart from "no rows at all".
    """
    c = _client(client)
    if not c:
        return []
    try:
        res = (c.table("scraper_health")
               .select("source_name,scraped_at,items_found,errors,status,"
                       "status_reason,consecutive_zeros")
               .order("scraped_at", desc=True)
               .limit(limit).execute())
    except Exception as exc:                      # noqa: BLE001
        logger.warning("supervisor: scraper_health read failed: %s", exc)
        return []

    latest: dict[str, dict] = {}
    for row in res.data or []:
        name = row.get("source_name")
        if name and name not in latest:
            latest[name] = row
    return list(latest.values())


def chronic_sources(client=None, days: int = CHRONIC_DAYS) -> set[str]:
    """
    Sources this agent already flagged as anomalous on >= `days - 1` earlier
    days inside the window.

    Read BEFORE today's findings are written, so a hit here plus an anomaly
    today makes `days` in a row. Distinct calendar dates inside a `days`-wide
    window cannot skip a day, so counting dates is an exact test for
    "consecutive" given one pass per day — no gap analysis needed.
    """
    events = recent_events(hours=24 * days, levels=("anomaly",), limit=500, client=client)
    per_source: dict[str, set[str]] = {}
    for event in events:
        if event.get("agent") != AGENT:
            continue
        name = event.get("source_name")
        stamp = _parse_ts(event.get("created_at"))
        if name and stamp:
            per_source.setdefault(name, set()).add(stamp.date().isoformat())
    return {name for name, dates in per_source.items() if len(dates) >= days - 1}


# ── Classification (pure — this is the part worth testing) ───────────────────

def _finding(level: str, code: str, message: str, source: str | None = None, **detail) -> dict:
    return {"level": level, "code": code, "message": message, "source": source, "detail": detail}


def classify_findings(*, pipeline_state, scraper_health=(), stuck=(), now=None) -> list[dict]:
    """
    Turn raw state rows into findings. No I/O, no side effects.

    Each finding is {"level": warning|anomaly, "code", "message", "source",
    "detail"}. `code` is the stable machine key — filter on it, not on message.
    """
    now = now or datetime.now(timezone.utc)
    findings: list[dict] = []
    down: set[str] = set()
    known: set[str] = set()

    for row in pipeline_state or []:
        name = row.get("source_name") or "?"
        known.add(name)
        status = (row.get("last_status") or "").strip().lower()
        failures = int(row.get("consecutive_failures") or 0)
        reason = row.get("last_reason") or "no reason recorded"

        if status in ("blocked", "unavailable"):
            down.add(name)
            level = "anomaly" if failures >= FAILURE_STREAK_ANOMALY else "warning"
            findings.append(_finding(
                level, f"source_{status}",
                f"{name}: {status} on {failures} consecutive pass(es) — {reason}",
                source=name, consecutive_failures=failures, last_reason=reason,
            ))
            # A blocked source is stale BY CONSTRUCTION — state_store only moves
            # last_run_at on 'ok'. Reporting it twice would double-count it
            # toward the 3-source serious threshold and tell the operator
            # nothing new, so the staleness check is skipped here.
            continue

        # last_run_at is "last successful pass". Missing means this source has
        # never completed one — worse than stale, not merely late.
        last_ok = _parse_ts(row.get("last_run_at"))
        if last_ok is None:
            findings.append(_finding(
                "anomaly", "source_never_ran",
                f"{name}: no successful pass on record (last_status={status or 'unset'})",
                source=name, last_status=status,
            ))
        elif now - last_ok > timedelta(hours=STALE_HOURS):
            hours = int((now - last_ok).total_seconds() // 3600)
            findings.append(_finding(
                "anomaly", "source_stale",
                f"{name}: no successful pass for {hours}h (last {last_ok.isoformat()})",
                source=name, hours_since_success=hours,
            ))

    # Every registered source down at once is a different failure mode from N
    # independent ones: sites do not coordinate redesigns. It means the network,
    # the credentials or the deploy. Note this fires regardless of failure
    # streaks — a fleet-wide first failure is already the signal.
    if len(known) >= MIN_FLEET_FOR_FLEETWIDE and down == known:
        findings.append(_finding(
            "anomaly", "all_sources_failing",
            f"All {len(known)} registered sources are blocked/unavailable — "
            f"this is almost certainly network or credentials, not the sites.",
            sources=sorted(known),
        ))

    # Only rows we can still believe. A row with no parseable timestamp is taken
    # at face value: the cutoff exists to discard rows PROVABLY older than the
    # window, not to throw away evidence over a formatting quirk.
    fresh_health, fossils = [], 0
    for row in scraper_health or []:
        stamp = _parse_ts(row.get("scraped_at"))
        if stamp and now - stamp > timedelta(hours=HEALTH_ROW_MAX_AGE_HOURS):
            fossils += 1
        else:
            fresh_health.append(row)

    # Rows exist but every one is old => the writer stopped, which is invisible
    # from the table itself (an append-only table that stops being appended to
    # looks exactly like a healthy quiet one). Warning, not anomaly: it is a
    # blind spot, not evidence of a broken source, and it must not push the
    # operator's email over the bar on its own.
    if fossils and not fresh_health:
        findings.append(_finding(
            "warning", "health_rows_stale",
            f"every scraper_health row is older than {HEALTH_ROW_MAX_AGE_HOURS}h "
            f"({fossils} row(s)) — the ingestion pass has stopped writing them, so "
            f"zero-streak checks are suppressed rather than graded on stale data",
            stale_rows=fossils,
        ))

    for row in fresh_health:
        zeros = int(row.get("consecutive_zeros") or 0)
        if zeros >= ZERO_STREAK_ANOMALY:
            name = row.get("source_name") or "?"
            findings.append(_finding(
                "anomaly", "zero_streak",
                f"{name}: 0 items for {zeros} consecutive runs — the listing "
                f"page probably changed shape (status={row.get('status')})",
                source=name, consecutive_zeros=zeros,
            ))

    for row in stuck or []:
        findings.append(_finding(
            "anomaly", "agent_stuck",
            f"{row.get('agent', '?')} has been 'running' since {row.get('started_at')} — "
            f"the container died mid-pass and never closed its run row",
            source=None, run_id=row.get("id"), agent=row.get("agent"),
        ))

    return findings


def is_serious(findings, chronic=()) -> list[str]:
    """
    Return the reasons this pass warrants an email. Empty list = log only.

    See the module docstring for why these four and nothing else.
    """
    reasons: list[str] = []
    anomalies = [f for f in findings if f["level"] == "anomaly"]
    anomalous = {f["source"] for f in anomalies if f["source"]}
    codes = {f["code"] for f in anomalies}

    if "all_sources_failing" in codes:
        reasons.append("EVERY source is failing — network, credentials or a bad deploy")

    if len(anomalous) >= SERIOUS_SOURCE_COUNT:
        reasons.append(
            f"{len(anomalous)} sources anomalous in one pass "
            f"({', '.join(sorted(anomalous))}) — too many to be independent"
        )

    chronic_hits = sorted(anomalous & set(chronic))
    if chronic_hits:
        reasons.append(
            f"broken {CHRONIC_DAYS} days running: {', '.join(chronic_hits)} — "
            f"not flaky, actually broken"
        )

    if "agent_stuck" in codes:
        reasons.append("an agent run is stuck in 'running' — a container died mid-pass")

    return reasons


# ── Email ────────────────────────────────────────────────────────────────────

def _compose_email(findings, reasons, checked: int) -> tuple[str, str]:
    anomalies = [f for f in findings if f["level"] == "anomaly"]
    warnings = [f for f in findings if f["level"] == "warning"]

    subject = f"[Yishun Again] Scraper fleet: {len(anomalies)} anomaly(s) across {checked} source(s)"

    lines = [
        "The supervisor found something that will not fix itself.",
        "",
        "WHY YOU ARE GETTING THIS EMAIL:",
    ]
    lines += [f"  - {reason}" for reason in reasons]
    lines += ["", f"ANOMALIES ({len(anomalies)}):"]
    lines += [f"  [{f['code']}] {f['message']}" for f in anomalies] or ["  (none)"]

    if warnings:
        lines += ["", f"WARNINGS — logged, not the reason for this email ({len(warnings)}):"]
        lines += [f"  [{f['code']}] {f['message']}" for f in warnings]

    lines += [
        "",
        "A single flaky source never triggers this email — only >= 3 sources at once,",
        "all sources failing, one source broken 3 days running, or a stuck agent run.",
        "",
        f"Full activity log: {war_room_url('/activity')}",
    ]
    return subject, "\n".join(lines) + footer()


# ── Public API ───────────────────────────────────────────────────────────────

def run(supabase_client=None, trigger: str = "scheduler", now=None) -> dict:
    """
    One supervision pass.

    `now` defaults to the wall clock; it exists so a test can pin the reference
    time. Without it, run()'s staleness checks read against real time while any
    fixture is frozen, so a fixture that was "recent" when written silently rots
    into a false anomaly a day later — which is exactly how this went red.

    Never raises: it runs unattended straight after the daily ingestion pass and
    must not be able to take down anything downstream of it.
    """
    stats = {"sources_checked": 0, "warnings": 0, "anomalies": 0,
             "serious": False, "emailed": False, "errors": 0}

    if not agent_enabled(AGENT):
        logger.info("supervisor: disabled via AGENT_DISABLED — skipping")
        stats["skipped"] = True
        return stats

    try:
        client = _client(supabase_client)
        with AgentRun(AGENT, trigger=trigger, client=client) as run_ctx:
            try:
                _supervise(run_ctx, client, stats, now=now)
            except Exception as exc:              # noqa: BLE001
                stats["errors"] += 1
                run_ctx.error_("supervisor_failed", f"supervision pass failed: {exc}")
            for key, value in stats.items():
                run_ctx.stat(key, value)
    except Exception as exc:                      # noqa: BLE001
        # Belt and braces: even the activity layer failing must not propagate.
        logger.exception("supervisor: unhandled failure: %s", exc)
        stats["errors"] += 1

    return stats


def _supervise(run_ctx, client, stats: dict, now=None) -> None:
    state = load_pipeline_state(client)
    health = load_scraper_health(client)
    stuck = stale_runs(older_than_minutes=STUCK_RUN_MINUTES, client=client)
    chronic = chronic_sources(client)

    stats["sources_checked"] = len(state)
    if not state:
        run_ctx.warn("no_pipeline_state",
                     "pipeline_state is empty — either nothing has run yet or the DB is unreachable")
        run_ctx.set_summary("No pipeline_state rows to supervise.")
        return

    findings = classify_findings(pipeline_state=state, scraper_health=health, stuck=stuck, now=now)

    for finding in findings:
        emit = run_ctx.anomaly if finding["level"] == "anomaly" else run_ctx.warn
        emit(finding["code"], finding["message"],
             source_name=finding["source"], **finding["detail"])
        stats["anomalies" if finding["level"] == "anomaly" else "warnings"] += 1

    if not findings:
        run_ctx.success("fleet_ok", f"All {len(state)} source(s) healthy")
        run_ctx.set_summary(f"{len(state)} sources checked, all healthy.")
        return

    reasons = is_serious(findings, chronic)
    stats["serious"] = bool(reasons)

    if not reasons:
        # The whole point of this agent: this is a normal bad day, not an outage.
        run_ctx.info("not_serious",
                     f"{stats['anomalies']} anomaly / {stats['warnings']} warning — "
                     f"below the email bar, logged only")
        run_ctx.set_summary(
            f"{len(state)} sources checked, {stats['anomalies']} anomaly / "
            f"{stats['warnings']} warning — logged, not emailed."
        )
        return

    subject, body = _compose_email(findings, reasons, len(state))
    # Date in the key, on top of the 24h window: the same broken set can mail at
    # most once per day even if the supervisor is re-run by hand.
    broken = "+".join(sorted({f["source"] for f in findings
                              if f["level"] == "anomaly" and f["source"]})) or "fleet"
    dedup = f"supervisor:{datetime.now(timezone.utc).date().isoformat()}:{broken}"

    result = notify("anomaly", subject, body,
                    dedup_key=dedup, throttle_minutes=1440, client=client)
    stats["emailed"] = result["status"] == "sent"
    run_ctx.info("operator_notified",
                 f"serious anomaly email {result['status']} (dedup={dedup})")
    run_ctx.set_summary(
        f"SERIOUS: {'; '.join(reasons)} — email {result['status']}."
    )
