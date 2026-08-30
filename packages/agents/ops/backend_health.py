"""
Backend health + cost guard (req #12).

Two jobs in one pass, because they answer the same question — "is the backend
still in a state I would want to leave running unattended overnight?"

  1. HEALTH. One `backend_health_checks` row per component (supabase,
     cloudflare_r2, anthropic, gemini, cost_guard) every run, so the War Room
     has a time series rather than a single "is it up right now".
  2. COST. The realistic way this project gets expensive is not a price change,
     it is a runaway scheduler: a misfiring trigger turning 1 pass/day into 48
     silently multiplies the LLM bill by 48. So the guard checks BOTH the
     estimated spend and the structural risks that produce it.

WHY THE API CHECKS DO NOT CALL THE APIS
---------------------------------------
A liveness probe that spends money is self-defeating in an agent whose job is
watching the bill — and a synthetic call proves the key works, which the real
pipeline already proves for free every night. So `anthropic` and `gemini` are
judged on evidence: is the key configured, and did the last 24h of real calls
produce auth/billing errors. Cheaper, and it reflects the traffic that matters.

Public API
----------
run(supabase_client=None, trigger="scheduler") -> dict
    {"components", "down", "degraded", "estimated_usd", "cost_tripped",
     "notified", "errors"}
"""

import logging
import os
import time
from datetime import datetime, timedelta, timezone

from ops.activity import AgentRun, agent_enabled, recent_events, recent_runs
from ops.notify import footer, notify, war_room_url

logger = logging.getLogger(__name__)

AGENT = "backend_health"

# Supabase is in the same region as Cloud Run; a trivial single-row select is
# tens of milliseconds. 3s means something is wrong upstream even though it
# technically answered.
SUPABASE_SLOW_MS = 3000

R2_ENDPOINT_TEMPLATE = "https://{account}.r2.cloudflarestorage.com"


# ── Cost model — ALL FIGURES ARE ESTIMATES, NOT BILLING DATA ─────────────────
# List price per 1M tokens (USD), input/output, as published 2026-07. Kept as a
# table rather than a single per-call number so the arithmetic below is
# auditable when prices move.
_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5":  (1.00, 5.00),    # Stage 2 classify (filters/stage2_writer.py)
    "claude-sonnet-4-6": (3.00, 15.00),   # Stage 2 write
}

# Assumed token shape of one Stage 2 call, (input, output). Input is system
# prompt + article body; output is bounded by the max_tokens set in
# stage2_writer (512 classify / 2048 write) and typically lands well under it.
# These are deliberately generous — a cost guard that under-estimates is a cost
# guard that never fires.
_STAGE2_SHAPE: dict[str, tuple[str, int, int]] = {
    "classify": ("claude-haiku-4-5",  2_500,   300),
    "write":    ("claude-sonnet-4-6", 3_000, 1_200),
}


def _call_usd(model: str, tokens_in: int, tokens_out: int) -> float:
    price_in, price_out = _USD_PER_MTOK[model]
    return (tokens_in * price_in + tokens_out * price_out) / 1_000_000


# One queued draft = one classify call + one write call. ~= $0.031 today.
STAGE2_USD_PER_DRAFT = round(sum(_call_usd(*shape) for shape in _STAGE2_SHAPE.values()), 5)

# Stage 1 runs on the Gemini free tier, which is capped by requests-per-day
# rather than billed (filters/stage1_quota.py). It therefore contributes $0 to
# spend while still being the thing that limits throughput. Overridable so a
# billing key can be priced in without a code change.
STAGE1_USD_PER_CALL = float(os.getenv("STAGE1_USD_PER_CALL", "0.0"))

# Daily spend that warrants waking the operator. At ~$0.031/draft this is ~64
# drafts a day — far above the 1-pass-a-day design, so tripping it means
# something ran that should not have.
COST_ALERT_USD_PER_DAY = float(os.getenv("COST_ALERT_USD_PER_DAY", "2.00"))

# The design is one scheduled ingestion pass per day. Two tolerates a manual
# re-run; more than that is a trigger firing on its own.
EXPECTED_PASSES_PER_DAY = 1
RUNAWAY_PASSES = 2

# Cloud Run does not expose its own min-instances setting to the container, so
# the deploy has to mirror it here. Unset reads as 0, which only ever
# under-reports — it can miss an idle-cost risk, never invent one.
MIN_INSTANCES_ENV = "CLOUD_RUN_MIN_INSTANCES"

# agent_runs.stats keys an agent may use to report its own model-call counts.
# These are summed ALONGSIDE pipeline_run_history. No agent writes them today —
# the ingestion pass records its counts as pipeline_run_history rows and its
# AgentRun stats are queue/publish counters, not call counts — so the two
# sources are disjoint. An agent that later writes both would be double-counted,
# which for a cost guard is the safe direction to be wrong in.
_STAGE1_STAT_KEYS = ("stage1_calls", "stage1_requests")
_STAGE2_STAT_KEYS = ("stage2_drafts", "stage2_calls", "drafts_written")


def _client(explicit=None):
    """Return a Supabase client, or None. Never raises."""
    if explicit is not None:
        return explicit
    try:
        from classifiers.corroboration import get_supabase_client
        return get_supabase_client()
    except Exception as exc:                      # noqa: BLE001
        logger.warning("backend_health: no Supabase client (%s)", exc)
        return None


def _check(component: str, status: str, message: str,
           latency_ms: int | None = None, **detail) -> dict:
    return {"component": component, "status": status, "message": message,
            "latency_ms": latency_ms, "detail": detail}


# ── Component checks ─────────────────────────────────────────────────────────

def check_supabase(client=None) -> dict:
    """Timed trivial query. 'down' on exception, 'degraded' when slow."""
    c = _client(client)
    if not c:
        return _check("supabase", "down", "No Supabase client — URL/secret key not configured")

    started = time.monotonic()
    try:
        c.table("sources").select("id").limit(1).execute()
    except Exception as exc:                      # noqa: BLE001
        elapsed = int((time.monotonic() - started) * 1000)
        return _check("supabase", "down", f"Query failed: {exc}"[:500], elapsed)

    elapsed = int((time.monotonic() - started) * 1000)
    if elapsed > SUPABASE_SLOW_MS:
        return _check("supabase", "degraded",
                      f"Reachable but slow: {elapsed}ms (threshold {SUPABASE_SLOW_MS}ms)",
                      elapsed)
    return _check("supabase", "ok", f"Reachable in {elapsed}ms", elapsed)


def check_r2() -> dict:
    """
    HEAD the R2 bucket.

    The existing R2 client (art/generate_pixel_art.py `_r2_client`) is not
    reused: importing that module constructs a Modal App and pulls in the modal
    package at import time, which is far too much to drag into a health check.
    The four-line boto3 construction below is the same shape.

    Missing config is 'unknown', never 'down' — the art pipeline is optional and
    absence of configuration is not evidence of an outage.
    """
    account = os.getenv("CF_R2_ACCOUNT_ID", "").strip()
    key_id = os.getenv("CF_R2_ACCESS_KEY_ID", "").strip()
    secret = os.getenv("CF_R2_SECRET_ACCESS_KEY", "").strip()
    bucket = os.getenv("CF_R2_BUCKET_NAME", "").strip()

    if not all((account, key_id, secret, bucket)):
        return _check("cloudflare_r2", "unknown",
                      "CF_R2_* not fully configured — art/asset uploads are disabled",
                      configured=False)

    started = time.monotonic()
    try:
        import boto3
        s3 = boto3.client(
            "s3",
            endpoint_url=R2_ENDPOINT_TEMPLATE.format(account=account),
            aws_access_key_id=key_id,
            aws_secret_access_key=secret,
            region_name="auto",
        )
        s3.head_bucket(Bucket=bucket)
    except Exception as exc:                      # noqa: BLE001
        elapsed = int((time.monotonic() - started) * 1000)
        return _check("cloudflare_r2", "down", f"head_bucket failed: {exc}"[:500],
                      elapsed, bucket=bucket)

    elapsed = int((time.monotonic() - started) * 1000)
    return _check("cloudflare_r2", "ok", f"Bucket {bucket} reachable in {elapsed}ms",
                  elapsed, bucket=bucket)


# Which recent errors count against which provider. Matching is on the event
# text, so a Stage 1 event is attributed to Gemini even when the message never
# names it.
_PROVIDER_MARKERS = {
    "anthropic": ("anthropic", "claude", "stage2", "stage 2", "haiku", "sonnet"),
    "gemini":    ("gemini", "google-genai", "genai", "stage1", "stage 1", "rpd"),
}
_BLOCKING_MARKERS = ("401", "403", "authentication", "invalid api key", "invalid x-api-key",
                     "credit balance", "billing", "permission_denied", "unauthenticated",
                     "payment required")
_THROTTLE_MARKERS = ("quota", "resource_exhausted", "429", "rate limit", "rpd")

_PROVIDER_KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY"}


def check_llm_provider(provider: str, events=()) -> dict:
    """
    Judge a model provider from configuration + the last 24h of real calls.

    'unknown' when the key is absent rather than 'down': this agent also runs
    locally and in CI where no keys are set, and a status that is 'down' on
    every developer machine stops meaning anything. A key that is set but
    rejected shows up as a blocking error below, which is the case that matters.
    """
    env_var = _PROVIDER_KEY_ENV.get(provider, "")
    if not os.getenv(env_var, "").strip():
        return _check(provider, "unknown", f"{env_var} not configured", configured=False)

    markers = _PROVIDER_MARKERS.get(provider, ())
    blocking, throttling = [], []
    for event in events or []:
        text = f"{event.get('event', '')} {event.get('message', '')}".lower()
        if not any(marker in text for marker in markers):
            continue
        if any(marker in text for marker in _BLOCKING_MARKERS):
            blocking.append(text[:200])
        elif any(marker in text for marker in _THROTTLE_MARKERS):
            throttling.append(text[:200])

    if blocking:
        return _check(provider, "down",
                      f"{len(blocking)} auth/billing failure(s) in 24h — calls are being "
                      f"rejected outright",
                      errors=blocking[:3])
    if throttling:
        # Quota clears on its own; a key rejection does not. Different severity.
        return _check(provider, "degraded",
                      f"{len(throttling)} quota/rate-limit event(s) in 24h — throughput "
                      f"capped but self-clearing",
                      errors=throttling[:3])
    return _check(provider, "ok", "Key configured, no auth or billing errors in 24h")


# ── Cost guard ───────────────────────────────────────────────────────────────

def _sum_stat(stats: dict, keys) -> int:
    total = 0
    for key in keys:
        value = (stats or {}).get(key)
        if isinstance(value, (int, float)):
            total += int(value)
    return total


def estimate_daily_cost(history=(), runs=()) -> dict:
    """
    Estimate the last 24h of model spend from what actually ran. Pure.

    Stage 1 calls come from each pass's per-source `novel` count (one candidate
    that survives dedup = one Stage 1 call) and Stage 2 drafts from
    `total_queued`. Both fall back to any counters an agent recorded in
    `agent_runs.stats`.

    Dry runs are counted: `dry_run` suppresses database writes, not the model
    calls, so a dry run costs exactly as much as a real one.
    """
    passes = 0
    stage1_calls = 0
    stage2_drafts = 0

    for row in history or []:
        passes += 1
        report = row.get("report") or {}
        for source in report.get("per_source") or []:
            novel = source.get("novel")
            if isinstance(novel, (int, float)):
                stage1_calls += int(novel)
        queued = row.get("total_queued")
        if not isinstance(queued, (int, float)):
            queued = report.get("total_queued") or 0
        stage2_drafts += int(queued or 0)

    for row in runs or []:
        stats = row.get("stats") or {}
        stage1_calls += _sum_stat(stats, _STAGE1_STAT_KEYS)
        stage2_drafts += _sum_stat(stats, _STAGE2_STAT_KEYS)

    usd = stage1_calls * STAGE1_USD_PER_CALL + stage2_drafts * STAGE2_USD_PER_DRAFT
    return {
        "passes": passes,
        "stage1_calls": stage1_calls,
        "stage2_drafts": stage2_drafts,
        "estimated_usd": round(usd, 4),
        "budget_usd": COST_ALERT_USD_PER_DAY,
    }


def assess_cost(estimate: dict, min_instances: int = 0) -> dict:
    """
    Turn an estimate into a health row. Pure.

    'down' is used for "over budget" because that is this agent's alarm
    condition and the schema's status vocabulary has no louder word; the message
    says what it really means. Structural risks alone are 'degraded' — they
    predict a bill rather than being one.
    """
    risks: list[str] = []
    passes = estimate["passes"]
    if passes > RUNAWAY_PASSES:
        risks.append(
            f"{passes} ingestion passes in 24h (design is {EXPECTED_PASSES_PER_DAY}/day) — "
            f"check for a duplicated APScheduler job or a stuck retry loop; this is the "
            f"realistic way the bill runs away"
        )
    if min_instances > 0:
        risks.append(
            f"Cloud Run min-instances = {min_instances} — the container is billed while "
            f"idle. This service is a scheduled batch job; min-instances should be 0"
        )

    over = estimate["estimated_usd"] > estimate["budget_usd"]
    detail = dict(estimate, min_instances=min_instances, risks=risks)

    if over:
        return _check("cost_guard", "down",
                      f"Estimated ${estimate['estimated_usd']:.2f} in 24h exceeds the "
                      f"${estimate['budget_usd']:.2f} alert threshold "
                      f"({estimate['stage2_drafts']} draft(s) over {passes} pass(es))",
                      **detail)
    if risks:
        return _check("cost_guard", "degraded",
                      f"Estimated ${estimate['estimated_usd']:.2f} in 24h is within budget, "
                      f"but {len(risks)} structural cost risk(s) found",
                      **detail)
    return _check("cost_guard", "ok",
                  f"Estimated ${estimate['estimated_usd']:.2f} in 24h "
                  f"(budget ${estimate['budget_usd']:.2f}, {passes} pass(es), "
                  f"{estimate['stage2_drafts']} draft(s))",
                  **detail)


def _min_instances() -> int:
    try:
        return int(os.getenv(MIN_INSTANCES_ENV, "0") or 0)
    except ValueError:
        return 0


# ── Alert composition ────────────────────────────────────────────────────────

def _compose_alert(checks, cost_tripped: bool) -> tuple[str, str]:
    down = [c for c in checks if c["status"] == "down"]
    infra_down = [c for c in down if c["component"] != "cost_guard"]

    if cost_tripped and not infra_down:
        subject = "[Yishun Again] Cost guard tripped"
    elif infra_down:
        subject = ("[Yishun Again] Backend DOWN: "
                   + ", ".join(c["component"] for c in infra_down))
    else:
        subject = "[Yishun Again] Backend health alert"

    lines = ["Backend health check found a hard failure.", ""]
    for check in checks:
        lines.append(f"  {check['status'].upper():<9} {check['component']:<16} {check['message']}")

    if cost_tripped:
        cost = next((c for c in checks if c["component"] == "cost_guard"), None)
        risks = (cost or {}).get("detail", {}).get("risks") or []
        lines += ["", "COST:"]
        lines += [f"  - {risk}" for risk in risks] or [
            "  - Spend is above the threshold with no structural risk detected — either "
            "raise COST_ALERT_USD_PER_DAY or find what ran."
        ]
        lines.append(f"  Per-draft estimate: ${STAGE2_USD_PER_DRAFT:.4f} "
                     f"(Stage 2 classify + write; ESTIMATE, not billing data)")

    lines += ["", f"War Room: {war_room_url('/health')}"]
    return subject, "\n".join(lines) + footer()


# ── Public API ───────────────────────────────────────────────────────────────

def run(supabase_client=None, trigger: str = "scheduler") -> dict:
    """
    Check every component, persist the results, alert only on a hard failure.

    Never raises — a health checker that can crash the pass is worse than none.
    """
    stats = {"components": 0, "down": 0, "degraded": 0, "estimated_usd": 0.0,
             "cost_tripped": False, "notified": False, "errors": 0}

    if not agent_enabled(AGENT):
        logger.info("backend_health: disabled via AGENT_DISABLED — skipping")
        stats["skipped"] = True
        return stats

    try:
        client = _client(supabase_client)
        with AgentRun(AGENT, trigger=trigger, client=client) as run_ctx:
            try:
                _health(run_ctx, client, stats)
            except Exception as exc:              # noqa: BLE001
                stats["errors"] += 1
                run_ctx.error_("health_check_failed", f"health pass failed: {exc}")
            for key, value in stats.items():
                run_ctx.stat(key, value)
    except Exception as exc:                      # noqa: BLE001
        logger.exception("backend_health: unhandled failure: %s", exc)
        stats["errors"] += 1

    return stats


def _health(run_ctx, client, stats: dict) -> None:
    events = recent_events(hours=24, levels=("error", "anomaly"), limit=400, client=client)

    checks = [
        check_supabase(client),
        check_r2(),
        check_llm_provider("anthropic", events),
        check_llm_provider("gemini", events),
    ]

    estimate = estimate_daily_cost(
        history=_load_run_history(client), runs=_load_agent_runs(client),
    )
    cost = assess_cost(estimate, _min_instances())
    checks.append(cost)

    _persist(client, checks)

    for check in checks:
        message = f"{check['component']}: {check['message']}"
        if check["status"] == "down":
            stats["down"] += 1
            run_ctx.error_(f"{check['component']}_down", message, **check["detail"])
        elif check["status"] == "degraded":
            stats["degraded"] += 1
            run_ctx.warn(f"{check['component']}_degraded", message, **check["detail"])
        elif check["status"] == "unknown":
            run_ctx.info(f"{check['component']}_unknown", message)
        else:
            run_ctx.success(f"{check['component']}_ok", message)

    stats["components"] = len(checks)
    stats["estimated_usd"] = estimate["estimated_usd"]
    stats["cost_tripped"] = cost["status"] == "down"

    if not (stats["down"] or stats["cost_tripped"]):
        run_ctx.set_summary(
            f"{len(checks)} component(s): {stats['degraded']} degraded, 0 down. "
            f"Est. ${estimate['estimated_usd']:.2f}/24h."
        )
        return

    subject, body = _compose_alert(checks, stats["cost_tripped"])
    # 60-minute throttle (the notify default for 'health'): long enough to stop a
    # storm, short enough that a real outage is not suppressed for a whole day.
    down_names = "+".join(sorted(c["component"] for c in checks if c["status"] == "down"))
    result = notify("health", subject, body, dedup_key=f"health:{down_names}", client=client)

    stats["notified"] = result["status"] == "sent"
    run_ctx.info("operator_notified", f"health alert {result['status']} ({down_names})")
    run_ctx.set_summary(
        f"{stats['down']} component(s) down"
        + (f", cost guard tripped at ${estimate['estimated_usd']:.2f}"
           if stats["cost_tripped"] else "")
        + f" — alert {result['status']}."
    )


def _load_run_history(client, hours: int = 24) -> list[dict]:
    if not client:
        return []
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    try:
        return (client.table("pipeline_run_history")
                .select("ran_at,dry_run,degraded,total_queued,report")
                .gte("ran_at", since)
                .order("ran_at", desc=True)
                .limit(200).execute().data) or []
    except Exception as exc:                      # noqa: BLE001
        logger.warning("backend_health: pipeline_run_history read failed: %s", exc)
        return []


def _load_agent_runs(client, hours: int = 24) -> list[dict]:
    return recent_runs(hours=hours, limit=200, client=client)


def _persist(client, checks) -> None:
    """Write one row per component. A failed write must not fail the check."""
    if not client:
        return
    try:
        client.table("backend_health_checks").insert([
            {"component": c["component"], "status": c["status"],
             "latency_ms": c["latency_ms"], "message": c["message"][:1000],
             "detail": c["detail"]}
            for c in checks
        ]).execute()
    except Exception as exc:                      # noqa: BLE001
        logger.warning("backend_health: could not persist %d check(s): %s", len(checks), exc)
