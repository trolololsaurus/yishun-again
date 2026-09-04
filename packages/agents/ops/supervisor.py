"""
Scraper supervisor (req #9) — watch the live sources, alert the operator only
when something is genuinely broken.

The pipeline already records everything this agent needs: `pipeline_state`
(per-source status + failure streak), `pipeline_run_history` (per-source fetch
counts, one row per pass) and `agent_runs`/`agent_events` (what the fleet
actually did). Nothing here scrapes or probes — it reads the record and decides
whether a human has to hear about it tonight.

WHY ZERO STREAKS COME FROM pipeline_run_history, NOT scraper_health
-------------------------------------------------------------------
`scraper_health.consecutive_zeros` is the obvious source for this and it is the
one this agent used to read. It was dead: that column's only writer,
`scrapers.log_scraper_run`, was reachable only from `scrapers.scrape_all`, which
lost its last caller when ingestion moved to `ingestion/sources/`. So the read
returned stale rows or none, and the zero-streak check could not fire no matter
how broken a source got — the precise failure this agent exists to catch.

`ingestion/health.py` has since given that table a live writer (one row per
fetched source per pass), and this agent still does not read it. That is a
standing decision, not a leftover: an append-only table that stops being appended
to looks exactly like a healthy quiet one, so a check whose whole job is
detecting silent death must not itself be able to go silent that way. The failure
above was not "the writer was missing", it was "the alert depended on a writer" —
replacing the writer does not remove that coupling.

`pipeline_run_history` is written by `state_store.record_run` at the end of every
real pass and carries `report.per_source[].fetched`, which is the same
measurement. Deriving the streak from it needs no new writes and no new table.
`scraper_health` remains the *display* surface (War Room health views,
`ops/maintenance.py`'s digest). Alerts belong here; reporting belongs there.

WHY THE ALERT BAR IS DELIBERATELY HIGH
--------------------------------------
An alerting system is only worth building if the operator still opens it in
month three. A single flaky source is normal: sites change markup, Cloudflare
has moods, a feed 404s for an afternoon and comes back. Alerting on every one
of those teaches the operator to mute the chat — and then the ONE alert that
mattered is invisible too. So a lone broken source is LOGGED (visible in War
Room) and not pushed.

A push (Telegram) is reserved for the four shapes that mean the archive has
stopped updating and will not fix itself:

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
    {"sources_checked", "warnings", "anomalies", "serious", "notified", "errors"}
"""

import logging
import re
from datetime import datetime, timedelta, timezone

from ingestion.health import ZERO_STREAK_WARNING as ZERO_STREAK_ANOMALY
from ops.activity import AgentRun, _client, agent_enabled, recent_events, stale_runs
from ops.notify import footer, notify, war_room_url

logger = logging.getLogger(__name__)

AGENT = "supervisor"

# ── Thresholds ───────────────────────────────────────────────────────────────
# A source that fails once is noise; three passes in a row is a pattern.
FAILURE_STREAK_ANOMALY = 3

# The pass runs daily, so 48h means two whole passes produced nothing. One
# missed pass is a Cloud Run hiccup; two is a stopped scheduler.
STALE_HOURS = 48

# Yishun-specific filtering legitimately returns 0 PUBLISHABLE items on most
# days, which is why "how many passes has this source fetched nothing" is even
# a question worth asking rather than an obvious "the site is dead".
#
# `fetched` (report.per_source[].fetched, read by zero_streaks() below) is
# POST-keyword-filter for EVERY source, primary tier included — not just
# discovery. Every `scrapers.scrape_*` module calls `content_matches_keywords`
# (or `content_matches_lang` for the Malay/Tamil scrapers) on the listing page
# before returning anything, and `ingestion/sources/legacy.py` says so outright
# ("The scraper has already applied the Yishun keyword filter"). A prior version
# of this file assumed primary `fetched` counted the raw listing page and gave
# it a 5-pass threshold on that basis — which was simply wrong, and it fired as
# false "anomalous" primary sources once the discovery tier (genuinely a
# different shape, see git history) was given its own longer leash instead of
# fixing the shared assumption underneath both. There is exactly one tier now:
# reuse `ingestion/health.py`'s ZERO_STREAK_WARNING, which reasons about the
# identical quantity for the identical reason ("Tamil Murasu or Berita Harian
# can go a month") — importing it rather than a second copy of the number keeps
# the two from drifting apart again.
# (ZERO_STREAK_ANOMALY itself is the `ZERO_STREAK_WARNING` import above, under
# this module's existing name — callers/tests read `sup.ZERO_STREAK_ANOMALY`.)

# How far back to look when computing that streak. Must comfortably exceed
# ZERO_STREAK_ANOMALY, or the threshold is unreachable by construction.
ZERO_STREAK_PASSES = 35

# An ingestion pass is minutes, not hours. Still 'running' after 90 minutes
# means the row was never closed — i.e. the process died.
STUCK_RUN_MINUTES = 90

# (a) and (c) from the module docstring.
SERIOUS_SOURCE_COUNT = 3
CHRONIC_DAYS = 3

# "Every source is failing" only carries information when there are enough
# sources for the word "every" to mean something. On a one- or two-source
# registration (a dev box, or a half-migrated fleet) it is just the per-source
# finding restated in a louder voice — and it would alert on every hiccup.
MIN_FLEET_FOR_FLEETWIDE = 3



# ── Discovery adapters vs primary scrapers ──────────────────────────────────
# Every outlet has a PRIMARY scraper (`straits_times`) and may also have
# DISCOVERY adapters — its own Google-News sitemap (`straits_times_sitemap`) or
# WordPress search (`mustsharenews_search`), a wider net BEHIND the primary. A
# discovery adapter failing while its outlet's primary is healthy is degraded
# archive depth, not an outage, and must not alert. Mirrors the War Room health
# demotion (`apps/war-room/lib/utils.isDiscoverySource`/`primaryIdOf`) — both
# suffixes strip to a real primary source id.
_DISCOVERY_SUFFIX = re.compile(r"_(sitemap|search)$")


def _is_discovery(source_name: str) -> bool:
    return bool(_DISCOVERY_SUFFIX.search(source_name or ""))


def _primary_id(source_name: str) -> str:
    return _DISCOVERY_SUFFIX.sub("", source_name or "")


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


def load_run_history(client=None, passes: int = ZERO_STREAK_PASSES) -> list[dict]:
    """
    The last `passes` ingestion reports, newest first. Returns [] on failure —
    which degrades to "no zero-streak findings", never to a crash.

    No dry-run filter is needed: `state_store.record_run` is only called on a
    real pass, so every row here is one.
    """
    c = _client(client)
    if not c:
        return []
    try:
        res = (c.table("pipeline_run_history")
               .select("ran_at,report")
               .order("ran_at", desc=True)
               .limit(passes).execute())
        return res.data or []
    except Exception as exc:                      # noqa: BLE001
        logger.warning("supervisor: pipeline_run_history read failed: %s", exc)
        return []


def zero_streaks(history) -> list[dict]:
    """
    Per-source count of consecutive most-recent passes that fetched nothing. Pure.

    `history` is newest-first. Walking back from the newest pass, a source's
    streak ends at the first pass where it fetched something.

    A pass where the source is ABSENT, or where it was blocked/unavailable, is
    skipped rather than counted or treated as breaking the streak — neither is
    evidence about whether the listing page still parses. Counting them would
    double-report the blocked source that `pipeline_state` already covers;
    letting them break the streak would let a source flapping between blocked and
    empty look healthy forever.
    """
    running: dict[str, int] = {}
    settled: set[str] = set()

    for row in history or []:
        report = row.get("report") if isinstance(row.get("report"), dict) else {}
        for source in report.get("per_source") or []:
            name = source.get("name")
            if not name or name in settled:
                continue
            if (source.get("status") or "").strip().lower() not in ("ok", "degraded"):
                continue                          # no evidence either way
            if int(source.get("fetched") or 0) > 0:
                settled.add(name)                 # streak broken; ignore older passes
                continue
            running[name] = running.get(name, 0) + 1

    return [{"source_name": name, "consecutive_zeros": count}
            for name, count in sorted(running.items())]


def chronic_sources(client=None, days: int = CHRONIC_DAYS) -> set[str]:
    """
    Sources this agent already flagged as anomalous on >= `days - 1` earlier
    days inside the window.

    Read BEFORE today's findings are written, so a hit here plus an anomaly
    today makes `days` in a row. Distinct calendar dates inside a `days`-wide
    window cannot skip a day, so counting dates is an exact test for
    "consecutive" given one pass per day — no gap analysis needed.
    """
    events = recent_events(hours=24 * days, levels=("anomaly",), limit=500,
                            agent=AGENT, client=client)
    per_source: dict[str, set[str]] = {}
    for event in events:
        name = event.get("source_name")
        stamp = _parse_ts(event.get("created_at"))
        if name and stamp:
            per_source.setdefault(name, set()).add(stamp.date().isoformat())
    return {name for name, dates in per_source.items() if len(dates) >= days - 1}


# ── Classification (pure — this is the part worth testing) ───────────────────

def _finding(level: str, code: str, message: str, source: str | None = None, **detail) -> dict:
    return {"level": level, "code": code, "message": message, "source": source, "detail": detail}


def classify_findings(*, pipeline_state, streaks=(), stuck=(), now=None) -> list[dict]:
    """
    Turn raw state rows into findings. No I/O, no side effects.

    Each finding is {"level": warning|anomaly, "code", "message", "source",
    "detail"}. `code` is the stable machine key — filter on it, not on message.
    """
    now = now or datetime.now(timezone.utc)
    findings: list[dict] = []
    down: set[str] = set()
    known: set[str] = set()

    # Outlets whose PRIMARY scraper reported healthy this pass — used below to
    # demote a covered discovery adapter's finding out of the alert path.
    healthy = {
        (row.get("source_name") or "?")
        for row in pipeline_state or []
        if (row.get("last_status") or "").strip().lower() == "ok"
    }

    def _covered_discovery(name: str | None) -> bool:
        return bool(name) and _is_discovery(name) and _primary_id(name) in healthy

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

    # A zero-streak is a WARNING, never an anomaly — it must not drive an alert.
    # `fetched` is 0 Yishun-MATCHING items, not 0 articles served: a single
    # outlet can legitimately go a month without a Yishun story (Tamil Murasu,
    # Berita Harian), and that silence is CORRELATED across the fleet (a quiet
    # week for one small town is quiet for everyone). So a batch of simultaneous
    # zero-streaks is the resting state, not "N independent breakages" — feeding
    # them into is_serious()'s ">=3 anomalous, too many to be independent" and
    # chronic checks alerted the operator "11 sources actually broken" every pass
    # for what was an ordinary quiet spell. A genuinely dead source surfaces on
    # its OWN path (status blocked/unavailable/error in pipeline_state), which
    # zero_streaks() deliberately skips. Logged + shown in the health views;
    # never a phone buzz.
    for row in streaks or []:
        zeros = int(row.get("consecutive_zeros") or 0)
        name = row.get("source_name") or "?"
        if zeros >= ZERO_STREAK_ANOMALY:
            findings.append(_finding(
                "warning", "zero_streak",
                f"{name}: 0 Yishun-matching items for {zeros} consecutive passes "
                f"— usually just a quiet source (an outlet can go a month without "
                f"a Yishun story), not a fault. A real failure shows as "
                f"blocked/unavailable/error, not a zero-streak.",
                source=name, consecutive_zeros=zeros,
            ))

    for row in stuck or []:
        findings.append(_finding(
            "anomaly", "agent_stuck",
            f"{row.get('agent', '?')} has been 'running' since {row.get('started_at')} — "
            f"the container died mid-pass and never closed its run row",
            source=None, run_id=row.get("id"), agent=row.get("agent"),
        ))

    # A covered discovery adapter's anomaly is degraded depth, not an outage:
    # demote it to a warning (logged, never emailed) as a post-pass so the
    # per-source creation sites above stay simple. If the outlet's primary is
    # ALSO down it is not in `healthy`, so the finding stays an anomaly — that
    # is a real outlet outage. See is_serious(): the alert rules count anomalies
    # only, so this single flip removes it from every one of them at once.
    for f in findings:
        if f["level"] == "anomaly" and _covered_discovery(f["source"]):
            f["level"] = "warning"
            f["message"] += (f" — discovery adapter; {_primary_id(f['source'])} "
                             f"primary feed OK, outlet still covered")

    return findings


def is_serious(findings, chronic=()) -> list[str]:
    """
    Return the reasons this pass warrants an alert. Empty list = log only.

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


# ── State-change dedup ─────────────────────────────────────────────────────
#
# `notify()`'s own dedup is exact-match on `dedup_key` within a time window —
# it stops a LITERAL repeat, not a re-description of the same standing problem
# under a slightly different key. The dedup key used to embed the sorted broken
# -source list, so any churn in which sources were anomalous (a source crossing
# in OR out) changed the key and the throttle never engaged — the same 8-ish
# broken sources alerted twice in one day with "slightly different" membership.
#
# The fix compares SIGNATURES, not keys: what was anomalous last time this
# agent actually emailed, vs what's anomalous now. Unchanged -> log only, don't
# re-alert. Changed (a source newly broken, or one that recovered) -> alerts.

# A quiet weekend, or the agent simply not firing an alert for a while because
# nothing was serious, must not make the next unchanged alert look brand new
# just because the LAST alert aged out of a short lookback window.
ALERT_SIGNATURE_LOOKBACK_HOURS = 24 * 14


def _alert_signature(findings) -> frozenset[str]:
    """
    "What's currently broken", as a comparable set. Pure.

    Anomalous sources make up most of it; `all_sources_failing` and
    `agent_stuck` have no `source` of their own, so each gets a pseudo-member —
    otherwise a fleet going from "3 sources down" to "every source down" (or an
    agent freshly stuck) with the same 3 sources still named would compare as
    unchanged and never re-alert.
    """
    anomalies = [f for f in findings if f["level"] == "anomaly"]
    sig = {f["source"] for f in anomalies if f["source"]}
    codes = {f["code"] for f in anomalies}
    if "all_sources_failing" in codes:
        sig.add("__all_sources_failing__")
    if "agent_stuck" in codes:
        sig.add("__agent_stuck__")
    return frozenset(sig)


def _previous_alert_signature(client=None) -> frozenset[str] | None:
    """
    The signature from the last time this agent actually emailed the operator.

    None means "no prior alert on record" (first ever, or aged past the
    lookback) — treated by the caller as a change, same fail-open spirit as
    `notify._recently_sent`: sending one extra alert is a nuisance, staying
    silent because the read failed or came up empty is the outage this agent
    exists to catch.

    Must be read BEFORE this pass writes its own `operator_notified` event, or
    a pass would compare itself to itself.
    """
    events = recent_events(hours=ALERT_SIGNATURE_LOOKBACK_HOURS, levels=("info",),
                            limit=100, agent=AGENT, client=client)
    for event in events:                          # newest first
        if event.get("event") == "operator_notified":
            sig = (event.get("detail") or {}).get("alert_signature")
            return frozenset(sig) if sig is not None else None
    return None


# ── Alert composition ────────────────────────────────────────────────────────────────────

def _compose_alert(findings, reasons, checked: int) -> tuple[str, str]:
    anomalies = [f for f in findings if f["level"] == "anomaly"]
    warnings = [f for f in findings if f["level"] == "warning"]

    subject = f"[Yishun Again] Scraper fleet: {len(anomalies)} anomaly(s) across {checked} source(s)"

    lines = [
        "The supervisor found something that will not fix itself.",
        "",
        "Why you're getting this:",
    ]
    lines += [f"  - {reason}" for reason in reasons]
    lines += ["", f"ANOMALIES ({len(anomalies)}):"]
    lines += [f"  [{f['code']}] {f['message']}" for f in anomalies] or ["  (none)"]

    if warnings:
        lines += ["", f"WARNINGS — logged, not the reason for this alert ({len(warnings)}):"]
        lines += [f"  [{f['code']}] {f['message']}" for f in warnings]

    lines += [
        "",
        "A single flaky source never triggers this alert — only >= 3 sources at once,",
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
             "serious": False, "notified": False, "errors": 0}

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
    streaks = zero_streaks(load_run_history(client))
    stuck = stale_runs(older_than_minutes=STUCK_RUN_MINUTES, client=client)
    chronic = chronic_sources(client)

    stats["sources_checked"] = len(state)
    if not state:
        run_ctx.warn("no_pipeline_state",
                     "pipeline_state is empty — either nothing has run yet or the DB is unreachable")
        run_ctx.set_summary("No pipeline_state rows to supervise.")
        return

    findings = classify_findings(pipeline_state=state, streaks=streaks, stuck=stuck, now=now)

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
                     f"below the alert bar, logged only")
        run_ctx.set_summary(
            f"{len(state)} sources checked, {stats['anomalies']} anomaly / "
            f"{stats['warnings']} warning — logged, not emailed."
        )
        return

    # Read BEFORE this pass's own operator_notified event exists, so an
    # unchanged standing problem compares against the LAST alert, not itself.
    signature = _alert_signature(findings)
    previous_signature = _previous_alert_signature(client)
    if previous_signature is not None and signature == previous_signature:
        run_ctx.info(
            "standing_anomaly",
            f"{'; '.join(reasons)} — unchanged since the last alert, not re-sending",
        )
        run_ctx.set_summary(
            f"SERIOUS but unchanged: {'; '.join(reasons)} — already alerted, not re-sent."
        )
        return

    subject, body = _compose_alert(findings, reasons, len(state))
    # Keyed on the signature itself, not the date: the state-change check above
    # is what stops a standing problem from re-alerting, so this key only needs
    # to make `notify()`'s own throttle a no-op AGREEING with that decision — a
    # date-only key would do the opposite and block a genuinely new problem
    # (say, a second, different source breaking later the same day) just
    # because something else already alerted today under that same date key.
    dedup = "supervisor:" + "+".join(sorted(signature))

    result = notify("anomaly", subject, body,
                    dedup_key=dedup, throttle_minutes=1440, client=client)
    stats["notified"] = result["status"] == "sent"
    run_ctx.info("operator_notified",
                 f"serious anomaly alert {result['status']} (dedup={dedup})",
                 alert_signature=sorted(signature))
    run_ctx.set_summary(
        f"SERIOUS: {'; '.join(reasons)} — alert {result['status']}."
    )
