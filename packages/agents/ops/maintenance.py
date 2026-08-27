"""
Maintenance digest (req #11) — read the error log, tell the operator in plain
English what is broken and what to do about it.

The activity log already says WHAT failed. This agent answers the two questions
a stack trace never does: what does that actually mean, and what do I type to
fix it. Every recognised failure signature maps to a (what happened, what to do)
pair in `_DIAGNOSES`, so the operator never has to remember that Gemini's daily
quota resets on US/Pacific midnight rather than SGT.

SILENCE MEANS HEALTHY
---------------------
At most one digest per day, and NOTHING at all when nothing is wrong. A "no
issues today" email every day is the fastest way to train someone to stop
reading the sender — and this is the same inbox the supervisor uses for real
outages. A clean run is recorded in `agent_runs` instead, where the War Room can
show it.

Public API
----------
run(supabase_client=None, trigger="scheduler") -> dict
    {"events_scanned", "issues", "failed_notifications", "emailed", "errors"}
"""

import logging
from datetime import datetime, timedelta, timezone

from ops.activity import AgentRun, agent_enabled, recent_events, recent_runs
from ops.notify import footer, notify, war_room_url

logger = logging.getLogger(__name__)

AGENT = "maintenance"

WINDOW_HOURS = 24
MAX_SAMPLES_PER_ISSUE = 3


# ── Diagnosis table ──────────────────────────────────────────────────────────
# Ordered: the FIRST match wins, so the distinctive signatures come first and
# the ambiguous ones (a bare "429" means one thing to a scraper and another to
# Stage 1) are disambiguated by `needs_source` rather than by string alone.
#
# `any`         — lowercase substrings; one hit is enough
# `needs_source`— True/False to require/forbid an event scoped to a source,
#                 None to not care
# `what` / `fix`— what the operator reads. Written for 3am, not for a reviewer.

_DIAGNOSES: tuple[dict, ...] = (
    {
        "id": "env_missing",
        "title": "Backend configuration missing",
        "any": ("supabase_url", "supabase_secret_key", "environmenterror",
                "must be set", "anthropic_api_key", "gemini_api_key"),
        "needs_source": None,
        "what": "A required environment variable is not set on the agents backend, so the "
                "affected stage cannot start at all. This is almost always a Cloud Run env "
                "var lost during a deploy — a new revision does not inherit vars that were "
                "set by hand on the previous one.",
        "fix": "Check the yishun-agents Cloud Run service -> Edit & Deploy New Revision -> "
               "Variables. Re-add the missing key and redeploy. Do NOT put it in a .env file "
               "in the repo.",
    },
    {
        "id": "anthropic_billing",
        "title": "Anthropic credit exhausted",
        "any": ("credit balance", "billing", "prepayment credits", "insufficient credit",
                "billingexhausted", "payment required", "402"),
        "needs_source": None,
        "what": "The Anthropic account is out of credit. Stage 2 is fully blocked: nothing "
                "can be classified or written, so the War Room queue will stay empty no "
                "matter how well the scrapers do.",
        "fix": "Top up at https://console.anthropic.com/settings/billing. Nothing else will "
               "clear this — it does not reset on a timer. Re-run the pass once topped up; "
               "watermarks were not advanced, so nothing was lost.",
    },
    {
        "id": "source_blocked",
        "title": "A source is refusing the scraper",
        "any": ("blocked", "403", "forbidden", "captcha", "bot detection",
                "scraperblocked", "sourceblocked", "cloudflare", "429"),
        "needs_source": True,
        "what": "The site is refusing the scraper — bot detection, a WAF rule or a rate "
                "limit. By design the scraper does NOT retry into a ban: it skips the source "
                "and leaves the watermark untouched, so the window is retried in full next "
                "pass and nothing is silently dropped.",
        "fix": "Open the source URL in a normal browser. If the page loads but the scrape "
               "does not, the markup changed — update the selector in "
               "packages/agents/scrapers/. If the page itself is challenging you, leave it: "
               "one blocked source is survivable and Google News RSS usually catches the "
               "same story.",
    },
    {
        "id": "stage1_quota",
        "title": "Gemini daily quota exhausted (Stage 1)",
        "any": ("rpd", "requests per day", "resource_exhausted", "quota",
                "rpdexhausted", "429", "stage1_halt"),
        "needs_source": None,
        "what": "Stage 1 hit the Gemini free-tier requests-per-day ceiling and halted the "
                "pass cleanly rather than burning the remaining candidates against a wall. "
                "Candidates that were not reached keep their watermark, so they are "
                "re-processed next pass — no coverage was lost, only delayed.",
        "fix": "The quota resets at midnight US/Pacific, NOT SGT — that is 15:00-16:00 SGT "
               "the same day. Check the live cap at https://aistudio.google.com/rate-limit; "
               "if the real ceiling is lower than the local estimate, lower STAGE1_RPD to "
               "match so the pass paces itself instead of discovering the wall.",
    },
    {
        "id": "api_auth",
        "title": "API key rejected",
        "any": ("401", "authentication_error", "invalid api key", "invalid x-api-key",
                "permission_error", "permission_denied", "unauthenticated"),
        "needs_source": None,
        "what": "A model provider rejected the key outright. Different from a quota error: "
                "this will not clear on its own and every call will fail until the key is "
                "replaced.",
        "fix": "Rotate the key in the provider console, then update it in the yishun-agents "
               "Cloud Run env vars and redeploy. Check the key was not truncated on paste — "
               "that is the usual cause when it worked yesterday.",
    },
    {
        "id": "db_infra",
        "title": "Database write rejected",
        "any": ("infraerror", "postgrest", "row-level security", "duplicate key",
                "violates", "check constraint", "null value in column"),
        "needs_source": None,
        "what": "Supabase refused a write. A CHECK-constraint rejection usually means code "
                "is writing a vocabulary value the migration does not allow yet (the exact "
                "shape of the bug migrations 009 and 011 existed to fix) — the insert is "
                "silently rejected and the data never lands.",
        "fix": "Read the constraint name in the message and compare it against "
               "packages/db/migrations/. If a migration is unapplied, apply it by hand in "
               "the Supabase SQL Editor — there is no migration runner (QA M15).",
    },
    {
        "id": "network",
        "title": "Network / timeout",
        "any": ("timeout", "timed out", "connection", "readtimeout", "ssl",
                "dns", "temporarily unavailable", "502", "503", "504"),
        "needs_source": None,
        "what": "A transient network failure. The fallback ladder already retried once "
                "before giving up, so a handful of these in a day is normal and self-healing.",
        "fix": "Nothing, unless it is the same host every pass — then the host is down or "
               "blocking Cloud Run's egress IPs rather than having a bad minute.",
    },
    {
        "id": "notify_failed",
        "title": "Operator Telegram alert could not be sent",
        "any": ("telegram http", "telegram_chat_id", "telegram_bot_token", "notify:"),
        "needs_source": None,
        "what": "An alert was recorded in the notifications ledger but the send failed. The "
                "content is not lost — but it means an alert you should have seen never "
                "reached Telegram.",
        "fix": "Check TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID on Cloud Run, and that the "
               "operator has started a chat with the bot (a bot cannot message a user who "
               "has never messaged it first). The unsent alerts are readable in the War Room "
               "notifications view.",
    },
)

_UNKNOWN = {
    "id": "unknown",
    "title": "Unrecognised error",
    "what": "This failure does not match any known signature, so there is no canned "
            "explanation for it.",
    "fix": "Read the full event (with its detail payload) in the War Room activity log. If "
           "it recurs, add a signature for it to _DIAGNOSES in "
           "packages/agents/ops/maintenance.py so the next person gets a real answer.",
}


def diagnose(text: str, source_name: str | None = None) -> dict:
    """Map a failure signature to a (what happened, what to do) pair."""
    haystack = (text or "").lower()
    for entry in _DIAGNOSES:
        needs = entry["needs_source"]
        if needs is True and not source_name:
            continue
        if needs is False and source_name:
            continue
        if any(token in haystack for token in entry["any"]):
            return entry
    return _UNKNOWN


# ── Signal collection ────────────────────────────────────────────────────────

def _signal(text, source=None, origin="event", when=None) -> dict:
    return {"text": (text or "").strip(), "source": source, "origin": origin, "when": when}


def collect_signals(*, events=(), runs=(), pipeline_state=(), health=(),
                    failed_notifications=()) -> list[dict]:
    """
    Flatten every failure record into one comparable list. Pure.

    Five sources on purpose: an error can be visible in only one of them. A
    crashed run leaves an `agent_runs.error` and no event; a source that failed
    quietly leaves only `pipeline_state.last_reason`; a scraper failure leaves a
    string in `scraper_health.errors`.
    """
    signals: list[dict] = []

    for event in events or []:
        signals.append(_signal(
            f"{event.get('event', '')} {event.get('message', '')}",
            source=event.get("source_name"), origin="event",
            when=event.get("created_at"),
        ))

    for run_row in runs or []:
        if run_row.get("status") in ("failed", "degraded") and run_row.get("error"):
            signals.append(_signal(
                f"{run_row.get('agent', '')} {run_row.get('error', '')}",
                origin="agent_run", when=run_row.get("started_at"),
            ))

    for row in pipeline_state or []:
        status = (row.get("last_status") or "").lower()
        if status and status != "ok" and row.get("last_reason"):
            signals.append(_signal(
                f"{status} {row.get('last_reason')}",
                source=row.get("source_name"), origin="pipeline_state",
                when=row.get("updated_at"),
            ))

    for row in health or []:
        for err in row.get("errors") or []:
            signals.append(_signal(
                err, source=row.get("source_name"), origin="scraper_health",
                when=row.get("scraped_at"),
            ))

    for row in failed_notifications or []:
        signals.append(_signal(
            f"notify: {row.get('subject', '')} {row.get('error', '')}",
            origin="notification", when=row.get("created_at"),
        ))

    return [s for s in signals if s["text"]]


def group_issues(signals) -> list[dict]:
    """
    Collapse signals into one entry per root cause, busiest first.

    Grouping by diagnosis rather than by message is the whole point: 40 events
    saying "429" across 8 sources is ONE thing to fix, and a digest that lists
    them individually is just the log again.
    """
    issues: dict[str, dict] = {}
    for signal in signals or []:
        entry = diagnose(signal["text"], signal["source"])
        issue = issues.setdefault(entry["id"], {
            "id": entry["id"], "title": entry["title"],
            "what": entry["what"], "fix": entry["fix"],
            "count": 0, "sources": set(), "origins": set(), "samples": [],
        })
        issue["count"] += 1
        if signal["source"]:
            issue["sources"].add(signal["source"])
        issue["origins"].add(signal["origin"])
        if len(issue["samples"]) < MAX_SAMPLES_PER_ISSUE:
            issue["samples"].append(signal["text"][:300])

    ordered = sorted(issues.values(), key=lambda i: i["count"], reverse=True)
    for issue in ordered:
        issue["sources"] = sorted(issue["sources"])
        issue["origins"] = sorted(issue["origins"])
    return ordered


# ── Email ────────────────────────────────────────────────────────────────────

def _compose_digest(issues, window_hours: int) -> tuple[str, str]:
    total = sum(issue["count"] for issue in issues)
    headline = issues[0]["title"] if issues else "Issues"
    subject = (f"[Yishun Again] Maintenance: {len(issues)} issue(s), "
               f"{total} event(s) — {headline}")

    lines = [
        f"What went wrong in the last {window_hours}h, grouped by root cause.",
        "You are getting this because something needs a decision or a fix — a clean",
        "day sends nothing at all.",
        "",
    ]
    for n, issue in enumerate(issues, 1):
        lines.append(f"{n}. {issue['title'].upper()}  ({issue['count']} event(s))")
        if issue["sources"]:
            lines.append(f"   Affected: {', '.join(issue['sources'])}")
        lines.append(f"   WHAT IT MEANS: {issue['what']}")
        lines.append(f"   WHAT TO DO:    {issue['fix']}")
        for sample in issue["samples"]:
            lines.append(f"     | {sample}")
        lines.append("")

    lines.append(f"Full activity log: {war_room_url('/activity')}")
    return subject, "\n".join(lines) + footer()


# ── Reads ────────────────────────────────────────────────────────────────────

def _client(explicit=None):
    """Return a Supabase client, or None. Never raises."""
    if explicit is not None:
        return explicit
    try:
        from classifiers.corroboration import get_supabase_client
        return get_supabase_client()
    except Exception as exc:                      # noqa: BLE001
        logger.warning("maintenance: no Supabase client (%s) — nothing to diagnose", exc)
        return None


def _since_iso(hours: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def _load(client, table: str, columns: str, build=None, limit: int = 200) -> list[dict]:
    """One guarded read. Returns [] on any failure — a digest is best-effort."""
    if not client:
        return []
    try:
        query = client.table(table).select(columns)
        if build is not None:
            query = build(query)
        return (query.limit(limit).execute().data) or []
    except Exception as exc:                      # noqa: BLE001
        logger.warning("maintenance: %s read failed: %s", table, exc)
        return []


# ── Public API ───────────────────────────────────────────────────────────────

def run(supabase_client=None, trigger: str = "scheduler") -> dict:
    """
    Build (and only if warranted, send) the daily maintenance digest.

    Never raises — it runs unattended and must not be able to break the pass it
    is reporting on.
    """
    stats = {"events_scanned": 0, "issues": 0, "failed_notifications": 0,
             "emailed": False, "errors": 0}

    if not agent_enabled(AGENT):
        logger.info("maintenance: disabled via AGENT_DISABLED — skipping")
        stats["skipped"] = True
        return stats

    try:
        client = _client(supabase_client)
        with AgentRun(AGENT, trigger=trigger, client=client) as run_ctx:
            try:
                _digest(run_ctx, client, stats)
            except Exception as exc:              # noqa: BLE001
                stats["errors"] += 1
                run_ctx.error_("maintenance_failed", f"digest build failed: {exc}")
            for key, value in stats.items():
                run_ctx.stat(key, value)
    except Exception as exc:                      # noqa: BLE001
        logger.exception("maintenance: unhandled failure: %s", exc)
        stats["errors"] += 1

    return stats


def _digest(run_ctx, client, stats: dict) -> None:
    since = _since_iso(WINDOW_HOURS)

    events = recent_events(hours=WINDOW_HOURS, levels=("error", "anomaly"),
                           limit=400, client=client)
    runs = recent_runs(hours=WINDOW_HOURS, limit=200, client=client)
    state = _load(client, "pipeline_state", "source_name,last_status,last_reason,updated_at")
    # scraper_health is live again: `ingestion/health.py` writes one row per
    # fetched source per pass, so this read carries real per-source error text
    # (the old writer inside scrapers.scrape_all was orphaned by the adapter port
    # and both are deleted). It is a reporting surface only — ops/supervisor.py
    # deliberately does NOT alert off this table; see its module docstring.
    health = _load(
        client, "scraper_health", "source_name,scraped_at,errors,status,status_reason",
        build=lambda q: q.gte("scraped_at", since).order("scraped_at", desc=True),
    )
    failed_notifications = _load(
        client, "notifications", "created_at,kind,subject,error,status",
        build=lambda q: q.eq("status", "failed").gte("created_at", since)
                         .order("created_at", desc=True),
        limit=50,
    )

    stats["events_scanned"] = len(events)
    stats["failed_notifications"] = len(failed_notifications)

    signals = collect_signals(events=events, runs=runs, pipeline_state=state,
                              health=health, failed_notifications=failed_notifications)
    issues = group_issues(signals)
    stats["issues"] = len(issues)

    if not issues:
        # Deliberately silent. See the module docstring.
        run_ctx.success("all_clear",
                        f"Nothing broken in the last {WINDOW_HOURS}h "
                        f"({len(runs)} run(s) reviewed) — no email sent")
        run_ctx.set_summary(f"Clean: 0 issues in {WINDOW_HOURS}h. No email sent.")
        return

    for issue in issues:
        run_ctx.info(f"issue_{issue['id']}",
                     f"{issue['title']}: {issue['count']} event(s)"
                     + (f" [{', '.join(issue['sources'])}]" if issue["sources"] else ""))
    if any(issue["id"] == "unknown" for issue in issues):
        run_ctx.warn("undiagnosed",
                     "Some failures matched no known signature — consider adding one "
                     "to _DIAGNOSES so the next digest explains it")

    subject, body = _compose_digest(issues, WINDOW_HOURS)
    dedup = f"maintenance:{datetime.now(timezone.utc).date().isoformat()}"
    result = notify("maintenance", subject, body, dedup_key=dedup, client=client)

    stats["emailed"] = result["status"] == "sent"
    run_ctx.info("digest_sent", f"maintenance digest {result['status']} (dedup={dedup})")
    run_ctx.set_summary(
        f"{len(issues)} issue(s) from {len(signals)} signal(s): "
        f"{', '.join(i['id'] for i in issues)} — email {result['status']}."
    )
