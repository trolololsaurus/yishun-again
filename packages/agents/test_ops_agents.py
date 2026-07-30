"""
Self-contained tests for the three ops agents. No pytest, no DB, no network.
Run: .venv/Scripts/python.exe test_ops_agents.py

These agents run unattended at 14:58 daily and email a human, so the two things
worth pinning down are the two things that are expensive to get wrong:

  1. THE EMAIL BAR (ops/supervisor.py). One flaky source must be logged and NOT
     emailed; three at once, all of them, a 3-day streak, or a stuck run must be.
     An alerting system the operator filters to spam is worse than none.
  2. THE COST ESTIMATE (ops/backend_health.py). The guard exists to catch a
     runaway scheduler, so the arithmetic and the trip thresholds are asserted
     against known counts rather than trusted.

Plus the hard rule all three share: run() must never raise, whatever the DB does.
"""
import importlib
import logging
import os
from datetime import datetime, timedelta, timezone
from unittest import mock

# These agents log every warning and error they find — including the ones the
# tests deliberately provoke. Left on, ~30 lines of alarming-looking output land
# before the first result line and bury it.
logging.disable(logging.CRITICAL)

sup = importlib.import_module("ops.supervisor")
mnt = importlib.import_module("ops.maintenance")
bh = importlib.import_module("ops.backend_health")

passed = failed = 0
def check(name, cond):
    global passed, failed
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    passed += 1 if cond else 0
    failed += 0 if cond else 1


# ── Fake Supabase ────────────────────────────────────────────────────────────
# Filters are intentionally ignored: these tests exercise agent logic, not
# PostgREST. Seed one table per read the agent under test performs.

class _Query:
    def __init__(self, table, rows, sink):
        self._table, self._rows, self._sink = table, rows, sink

    def insert(self, payload, *a, **kw):
        rows = payload if isinstance(payload, list) else [payload]
        self._sink.setdefault(self._table, []).extend(rows)
        return _Query(self._table, [], self._sink)

    def select(self, *a, **kw):  return self
    def update(self, *a, **kw):  return self
    def upsert(self, *a, **kw):  return self
    def eq(self, *a, **kw):      return self
    def gte(self, *a, **kw):     return self
    def lt(self, *a, **kw):      return self
    def in_(self, *a, **kw):     return self
    def order(self, *a, **kw):   return self
    def limit(self, *a, **kw):   return self
    def execute(self):           return mock.Mock(data=list(self._rows))


class FakeSupabase:
    def __init__(self, **tables):
        self.tables = tables
        self.inserted: dict[str, list] = {}

    def table(self, name):
        return _Query(name, self.tables.get(name, []), self.inserted)


def boom_client():
    """A client whose every call explodes — the DB-outage case."""
    client = mock.MagicMock()
    client.table.side_effect = RuntimeError("supabase exploded")
    return client


SENT = {"status": "sent", "id": "n-1", "error": None}
NOW = datetime(2026, 7, 20, 14, 58, tzinfo=timezone.utc)


def state(name, status="ok", failures=0, hours_ago=1, reason=None):
    stamp = None if hours_ago is None else (NOW - timedelta(hours=hours_ago)).isoformat()
    return {"source_name": name, "last_status": status, "watermark": "2026-07-19",
            "consecutive_failures": failures, "last_run_at": stamp,
            "last_reason": reason, "updated_at": stamp}


def codes(findings):
    return [f["code"] for f in findings]


def levels_for(findings, source):
    return {f["level"] for f in findings if f["source"] == source}


# ── supervisor: anomaly classification ───────────────────────────────────────

print("supervisor — anomaly classification:")

f1 = sup.classify_findings(pipeline_state=[state("cna", "blocked", failures=1)], now=NOW)
check("blocked once -> warning, not anomaly", codes(f1) == ["source_blocked"] and f1[0]["level"] == "warning")

f2 = sup.classify_findings(pipeline_state=[state("cna", "blocked", failures=3)], now=NOW)
check("blocked 3 passes in a row -> anomaly", f2[0]["level"] == "anomaly")

f3 = sup.classify_findings(pipeline_state=[state("stomp", "unavailable", failures=4)], now=NOW)
check("unavailable streak -> anomaly with its own code",
      codes(f3) == ["source_unavailable"] and f3[0]["level"] == "anomaly")

f4 = sup.classify_findings(pipeline_state=[state("cna", "ok", hours_ago=60)], now=NOW)
check("no successful pass for 60h -> stale anomaly",
      codes(f4) == ["source_stale"] and f4[0]["level"] == "anomaly")

f5 = sup.classify_findings(pipeline_state=[state("cna", "ok", hours_ago=47)], now=NOW)
check("47h is inside the 48h window -> nothing", f5 == [])

f6 = sup.classify_findings(pipeline_state=[state("cna", "ok", hours_ago=None)], now=NOW)
check("source that never completed a pass -> anomaly", codes(f6) == ["source_never_ran"])

# last_run_at only advances on 'ok', so every blocked source is also stale.
f7 = sup.classify_findings(
    pipeline_state=[state("cna", "blocked", failures=5, hours_ago=200)], now=NOW)
check("blocked source is not ALSO reported as stale (double-count guard)",
      codes(f7) == ["source_blocked"])

# ── supervisor: zero streaks (0 items is the normal case, not a failure) ─────

f8 = sup.classify_findings(
    pipeline_state=[state("stomp")],
    streaks=[{"source_name": "stomp", "consecutive_zeros": 4}],
    now=NOW)
check("4 zero-item runs -> nothing (Yishun filter legitimately returns 0)", f8 == [])

f9 = sup.classify_findings(
    pipeline_state=[state("stomp")],
    streaks=[{"source_name": "stomp", "consecutive_zeros": 5}],
    now=NOW)
check("5 zero-item runs in a row -> anomaly",
      codes(f9) == ["zero_streak"] and f9[0]["level"] == "anomaly")


# ── supervisor: deriving those streaks from the LIVE surface ─────────────────
# The streak is derived from pipeline_run_history, which every real pass writes.
# scraper_health does have a live writer again (ingestion/health.py), but this
# check deliberately stays off it — see the supervisor's module docstring: an
# alert for silent death must not depend on a writer that can itself go silent.

print("\nsupervisor — zero streaks from pipeline_run_history:")


def hist(*passes):
    """Newest-first run history. Each pass is {source: (status, fetched)}."""
    return [{"ran_at": f"2026-07-{20 - n:02d}T14:58:00+00:00", "dry_run": False,
             "report": {"per_source": [
                 {"name": name, "status": status, "fetched": fetched}
                 for name, (status, fetched) in p.items()]}}
            for n, p in enumerate(passes)]


def zeros_for(streaks, source):
    return next((s["consecutive_zeros"] for s in streaks if s["source_name"] == source), 0)


s1 = sup.zero_streaks(hist(*[{"stomp": ("ok", 0)}] * 6))
check("six empty passes -> streak of 6", zeros_for(s1, "stomp") == 6)

s2 = sup.zero_streaks(hist({"stomp": ("ok", 0)}, {"stomp": ("ok", 0)},
                           {"stomp": ("ok", 7)}, {"stomp": ("ok", 0)}))
check("the streak stops at the last pass that fetched something",
      zeros_for(s2, "stomp") == 2)

s3 = sup.zero_streaks(hist({"stomp": ("blocked", 0)}, {"stomp": ("ok", 0)},
                           {"stomp": ("ok", 0)}))
check("a blocked pass is no evidence — skipped, not counted (pipeline_state "
      "already reports it)", zeros_for(s3, "stomp") == 2)

s4 = sup.zero_streaks(hist({"cna": ("ok", 3)}, {"stomp": ("ok", 0)},
                           {"stomp": ("ok", 0)}))
check("a source absent from a pass does not break its streak",
      zeros_for(s4, "stomp") == 2)
check("...and a source that fetched something has no streak at all",
      zeros_for(s4, "cna") == 0)

check("no history at all -> no streaks, not an error", sup.zero_streaks([]) == [])
check("a malformed history row is ignored rather than fatal",
      sup.zero_streaks([{"ran_at": "x", "report": None}]) == [])

check("the lookback window must exceed the anomaly threshold, or it can never fire",
      sup.ZERO_STREAK_PASSES > sup.ZERO_STREAK_ANOMALY)

streak_db = FakeSupabase(
    pipeline_state=[state("stomp"), state("cna"), state("zaobao")],
    pipeline_run_history=hist(*[{"stomp": ("ok", 0), "cna": ("ok", 4)}] * 6))
with mock.patch.object(sup, "notify", return_value=SENT):
    stats = sup.run(supabase_client=streak_db, now=NOW)
check("run() reads the history table and raises the anomaly end to end",
      stats["anomalies"] == 1 and stats["errors"] == 0)

# ── supervisor: fleet-wide + stuck runs ──────────────────────────────────────

f10 = sup.classify_findings(pipeline_state=[
    state("cna", "blocked", failures=1), state("stomp", "blocked", failures=1),
    state("zaobao", "unavailable", failures=1)], now=NOW)
check("all sources down -> all_sources_failing anomaly on top of the per-source rows",
      "all_sources_failing" in codes(f10))

f11 = sup.classify_findings(pipeline_state=[
    state("cna", "blocked", failures=1), state("stomp", "blocked", failures=1),
    state("zaobao", "ok")], now=NOW)
check("some sources down -> no all_sources_failing", "all_sources_failing" not in codes(f11))

f11b = sup.classify_findings(pipeline_state=[
    state("cna", "blocked", failures=1), state("stomp", "blocked", failures=1)], now=NOW)
check("'every source' on a 2-source fleet is not evidence of anything",
      "all_sources_failing" not in codes(f11b))

f12 = sup.classify_findings(
    pipeline_state=[state("cna")],
    stuck=[{"id": "r-1", "agent": "ingestion", "started_at": "2026-07-20T09:00:00+00:00"}],
    now=NOW)
check("run stuck in 'running' -> agent_stuck anomaly",
      codes(f12) == ["agent_stuck"] and f12[0]["level"] == "anomaly")


# ── supervisor: what counts as SERIOUS enough to email ───────────────────────

print("\nsupervisor — the email bar:")

one_bad = sup.classify_findings(pipeline_state=[
    state("cna", "blocked", failures=4), state("stomp"), state("mothership")], now=NOW)
check("one broken source is logged, NOT emailed", sup.is_serious(one_bad) == [])

warn_only = sup.classify_findings(pipeline_state=[
    state("cna", "blocked", failures=1), state("stomp", "unavailable", failures=1),
    state("mothership", "blocked", failures=2), state("zaobao")], now=NOW)
check("three sources at warning level only -> not serious", sup.is_serious(warn_only) == [])

three_bad = sup.classify_findings(pipeline_state=[
    state("cna", "blocked", failures=4), state("stomp", "unavailable", failures=3),
    state("zaobao", "ok", hours_ago=99), state("mothership")], now=NOW)
r_three = sup.is_serious(three_bad)
check("three anomalous sources in one pass -> serious", len(r_three) >= 1)
check("...and the reason names the count", any("3 sources" in r for r in r_three))

all_bad = sup.classify_findings(pipeline_state=[
    state("cna", "blocked", failures=1), state("stomp", "blocked", failures=1),
    state("zaobao", "unavailable", failures=1)], now=NOW)
check("ALL sources failing -> serious even at warning-level streaks",
      any("EVERY source" in r for r in sup.is_serious(all_bad)))

chronic = sup.is_serious(
    sup.classify_findings(pipeline_state=[state("cna", "blocked", failures=4),
                                          state("stomp")], now=NOW),
    chronic={"cna"})
check("one source broken 3 days running -> serious", any("days running" in r for r in chronic))
check("...but the same source without history is not", sup.is_serious(
    sup.classify_findings(pipeline_state=[state("cna", "blocked", failures=4),
                                          state("stomp")], now=NOW), chronic=set()) == [])

stuck_serious = sup.is_serious(sup.classify_findings(
    pipeline_state=[state("cna")],
    stuck=[{"id": "r-1", "agent": "ingestion", "started_at": "x"}], now=NOW))
check("a stuck agent run is serious on its own", any("stuck" in r for r in stuck_serious))

check("no findings at all -> nothing to email", sup.is_serious([]) == [])


# ── supervisor: run() end to end ─────────────────────────────────────────────

print("\nsupervisor — run():")

healthy = FakeSupabase(pipeline_state=[state("cna"), state("stomp")])
with mock.patch.object(sup, "notify", return_value=SENT) as notify_healthy:
    # Pin now=NOW: fixtures are built relative to NOW, so run() must judge
    # staleness against the same clock or a day-old fixture reads as stale.
    stats = sup.run(supabase_client=healthy, now=NOW)
check("healthy fleet -> no email", notify_healthy.call_count == 0)
check("healthy fleet -> counted, no anomalies",
      stats["sources_checked"] == 2 and stats["anomalies"] == 0 and stats["errors"] == 0)
check("healthy run still writes an agent_runs row", len(healthy.inserted.get("agent_runs", [])) == 1)

broken = FakeSupabase(pipeline_state=[
    state("cna", "blocked", failures=4), state("stomp", "unavailable", failures=3),
    state("zaobao", "ok", hours_ago=99)])
with mock.patch.object(sup, "notify", return_value=SENT) as notify_broken:
    stats = sup.run(supabase_client=broken, now=NOW)
check("serious anomaly -> exactly one email", notify_broken.call_count == 1)
check("...sent as an anomaly", notify_broken.call_args.args[0] == "anomaly")
kwargs = notify_broken.call_args.kwargs
check("...deduped per day so a broken source cannot mail twice",
      kwargs["dedup_key"].startswith("supervisor:") and kwargs["throttle_minutes"] == 1440)
check("...with the broken sources in the key", "cna" in kwargs["dedup_key"])
check("...and marked serious in the stats", stats["serious"] is True and stats["emailed"] is True)
check("anomaly events were logged, not just emailed",
      len(broken.inserted.get("agent_events", [])) >= 3)


# ── maintenance: signature -> diagnosis ──────────────────────────────────────

print("\nmaintenance — diagnosis:")

check("RPD 429 from Stage 1 -> Gemini quota",
      mnt.diagnose("RpdExhaustedError: 429 RESOURCE_EXHAUSTED")["id"] == "stage1_quota")
check("...and the fix names the US/Pacific reset, not SGT",
      "US/Pacific" in mnt.diagnose("rpd exhausted")["fix"])
check("credit balance -> Anthropic billing",
      mnt.diagnose("Your credit balance is too low")["id"] == "anthropic_billing")
check("...and says Stage 2 is fully blocked",
      "Stage 2 is fully blocked" in mnt.diagnose("billing error")["what"])
check("403 on a source -> bot detection, not a quota problem",
      mnt.diagnose("ScraperBlocked: 403 Forbidden", source_name="stomp")["id"] == "source_blocked")
check("...and explains the no-retry-into-a-ban design",
      "does NOT retry" in mnt.diagnose("blocked", source_name="stomp")["what"])
check("a 429 WITH a source is the site, not Gemini",
      mnt.diagnose("HTTP 429 rate limited", source_name="zaobao")["id"] == "source_blocked")
check("a 429 with NO source falls through to Stage 1 quota",
      mnt.diagnose("HTTP 429 rate limited")["id"] == "stage1_quota")
check("missing env var -> Cloud Run config",
      mnt.diagnose("EnvironmentError: SUPABASE_URL and SUPABASE_SECRET_KEY must be set"
                   )["id"] == "env_missing")
check("...and points at the redeploy, not at .env",
      "Cloud Run" in mnt.diagnose("SUPABASE_SECRET_KEY must be set")["fix"])
check("401 -> key rejected (distinct from quota)",
      mnt.diagnose("authentication_error: invalid x-api-key")["id"] == "api_auth")
check("CHECK constraint -> unapplied migration",
      mnt.diagnose('new row violates check constraint "training_signals_action_check"'
                   )["id"] == "db_infra")
check("unrecognised error -> generic, still actionable",
      mnt.diagnose("flurb the widget sideways")["id"] == "unknown")
check("...and tells the operator where to look",
      "War Room" in mnt.diagnose("flurb")["fix"])


# ── maintenance: grouping by root cause ──────────────────────────────────────

print("\nmaintenance — grouping:")

signals = mnt.collect_signals(
    events=[{"event": "stage1_halt", "message": "429 RESOURCE_EXHAUSTED quota", "source_name": None},
            {"event": "stage1_halt", "message": "rpd exhausted", "source_name": None},
            {"event": "source_blocked", "message": "403 Forbidden", "source_name": "stomp"}],
    runs=[{"agent": "ingestion", "status": "failed", "error": "credit balance too low",
           "started_at": NOW.isoformat()}],
    pipeline_state=[{"source_name": "zaobao", "last_status": "blocked",
                     "last_reason": "captcha challenge", "updated_at": NOW.isoformat()}],
    health=[{"source_name": "shinmin", "errors": ["ReadTimeout on listing page"],
             "scraped_at": NOW.isoformat()}],
    failed_notifications=[{"subject": "test", "error": "Resend HTTP 422",
                           "created_at": NOW.isoformat()}],
)
check("every failure surface is collected", len(signals) == 7)

issues = mnt.group_issues(signals)
by_id = {i["id"]: i for i in issues}
check("two Gemini quota events collapse into ONE issue", by_id["stage1_quota"]["count"] == 2)
check("blocked sources group together across surfaces",
      by_id["source_blocked"]["count"] == 2
      and by_id["source_blocked"]["sources"] == ["stomp", "zaobao"])
check("billing surfaces from an agent_runs error with no event",
      "anthropic_billing" in by_id)
check("timeout from scraper_health.errors is diagnosed", by_id["network"]["count"] == 1)
check("failed notification is its own issue", "notify_failed" in by_id)
check("issues are ordered busiest first",
      [i["count"] for i in issues] == sorted([i["count"] for i in issues], reverse=True))
check("each issue carries both halves of the answer",
      all(i["what"] and i["fix"] for i in issues))
check("blank signals are dropped", mnt.collect_signals(
    events=[{"event": "", "message": ""}]) == [])


# ── maintenance: silence means healthy ───────────────────────────────────────

print("\nmaintenance — run():")

clean = FakeSupabase(pipeline_state=[{"source_name": "cna", "last_status": "ok",
                                      "last_reason": None, "updated_at": NOW.isoformat()}])
with mock.patch.object(mnt, "notify", return_value=SENT) as notify_clean:
    stats = mnt.run(supabase_client=clean)
check("nothing wrong -> NOTHING sent", notify_clean.call_count == 0)
check("...but the clean run is still recorded",
      stats["issues"] == 0 and len(clean.inserted.get("agent_runs", [])) == 1)

dirty = FakeSupabase(agent_events=[
    {"created_at": NOW.isoformat(), "agent": "ingestion", "level": "error",
     "event": "stage2_failed", "message": "Your credit balance is too low",
     "source_name": None, "detail": {}}])
with mock.patch.object(mnt, "notify", return_value=SENT) as notify_dirty:
    stats = mnt.run(supabase_client=dirty)
check("something wrong -> exactly one digest", notify_dirty.call_count == 1)
check("...sent as kind=maintenance (1/day throttle)",
      notify_dirty.call_args.args[0] == "maintenance")
check("...deduped by date", notify_dirty.call_args.kwargs["dedup_key"].startswith("maintenance:"))
check("...and the body carries the fix, not just the error",
      "console.anthropic.com" in notify_dirty.call_args.args[2])


# ── backend_health: the cost model ───────────────────────────────────────────

print("\nbackend_health — cost estimate:")

check("Stage 2 draft costs the published haiku+sonnet list price (~$0.031)",
      abs(bh.STAGE2_USD_PER_DRAFT - 0.031) < 1e-9)
check("Stage 1 is free-tier -> $0 by default", bh.STAGE1_USD_PER_CALL == 0.0)
check("default daily budget is $2.00", bh.COST_ALERT_USD_PER_DAY == 2.00)

history = [{"ran_at": NOW.isoformat(), "dry_run": False, "total_queued": 4,
            "report": {"per_source": [{"name": "cna", "novel": 10},
                                      {"name": "stomp", "novel": 5}]}}]
est = bh.estimate_daily_cost(history=history)
check("Stage 1 calls = novel candidates across sources", est["stage1_calls"] == 15)
check("Stage 2 drafts = queued items", est["stage2_drafts"] == 4)
check("spend = drafts x per-draft estimate",
      abs(est["estimated_usd"] - round(4 * bh.STAGE2_USD_PER_DRAFT, 4)) < 1e-9)
check("one pass counted", est["passes"] == 1)

dry = bh.estimate_daily_cost(history=[{"ran_at": NOW.isoformat(), "dry_run": True,
                                       "total_queued": 2, "report": {"per_source": []}}])
check("dry runs still cost money (they call the models, they just don't write)",
      dry["stage2_drafts"] == 2 and dry["estimated_usd"] > 0)

with_runs = bh.estimate_daily_cost(
    history=history, runs=[{"stats": {"stage1_calls": 7, "stage2_drafts": 3}}])
check("agent_runs.stats counters are added in",
      with_runs["stage1_calls"] == 22 and with_runs["stage2_drafts"] == 7)
check("non-numeric stats are ignored, not crashed on",
      bh.estimate_daily_cost(runs=[{"stats": {"stage2_drafts": "lots"}}])["stage2_drafts"] == 0)
check("no history at all -> zero, not an error",
      bh.estimate_daily_cost()["estimated_usd"] == 0.0)


# ── backend_health: when the guard trips ─────────────────────────────────────

print("\nbackend_health — cost guard:")

ok_cost = bh.assess_cost(bh.estimate_daily_cost(history=history), min_instances=0)
check("one pass, 4 drafts -> ok", ok_cost["status"] == "ok")

runaway = [dict(history[0]) for _ in range(48)]
over = bh.assess_cost(bh.estimate_daily_cost(history=runaway), min_instances=0)
check("48 passes of 4 drafts blows the $2 budget -> down", over["status"] == "down")
check("...and the runaway scheduler is named as the cause",
      any("ingestion passes in 24h" in r for r in over["detail"]["risks"]))

few = bh.assess_cost(bh.estimate_daily_cost(history=[dict(history[0]) for _ in range(3)]),
                     min_instances=0)
check("3 passes/day is under budget but structurally wrong -> degraded",
      few["status"] == "degraded")
check("2 passes/day tolerates a manual re-run -> ok",
      bh.assess_cost(bh.estimate_daily_cost(history=[dict(history[0]) for _ in range(2)]),
                     min_instances=0)["status"] == "ok")

idle = bh.assess_cost(bh.estimate_daily_cost(history=history), min_instances=1)
check("min-instances > 0 on a batch service -> degraded", idle["status"] == "degraded")
check("...and says why it costs money", any("idle" in r for r in idle["detail"]["risks"]))


# ── backend_health: component checks ─────────────────────────────────────────

print("\nbackend_health — components:")

sb_ok = bh.check_supabase(FakeSupabase(sources=[{"id": "s-1"}]))
check("reachable Supabase -> ok with a latency reading",
      sb_ok["status"] == "ok" and sb_ok["latency_ms"] is not None)
check("unreachable Supabase -> down", bh.check_supabase(boom_client())["status"] == "down")

with mock.patch.dict(os.environ, {"CF_R2_ACCOUNT_ID": "", "CF_R2_ACCESS_KEY_ID": "",
                                  "CF_R2_SECRET_ACCESS_KEY": "", "CF_R2_BUCKET_NAME": ""}):
    r2 = bh.check_r2()
check("unconfigured R2 -> unknown, NOT down", r2["status"] == "unknown")

with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
    check("no API key -> unknown (this agent also runs with no keys locally)",
          bh.check_llm_provider("anthropic")["status"] == "unknown")

with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}):
    check("key set, no errors in 24h -> ok",
          bh.check_llm_provider("anthropic", [])["status"] == "ok")
    check("billing error -> down (will not clear on its own)",
          bh.check_llm_provider("anthropic", [
              {"event": "stage2_failed", "message": "claude: credit balance too low"}
          ])["status"] == "down")
    check("an unrelated source failure is not blamed on the provider",
          bh.check_llm_provider("anthropic", [
              {"event": "source_blocked", "message": "stomp 403"}
          ])["status"] == "ok")

with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "g-test"}):
    check("quota error -> degraded (self-clearing), not down",
          bh.check_llm_provider("gemini", [
              {"event": "stage1_halt", "message": "gemini RESOURCE_EXHAUSTED quota"}
          ])["status"] == "degraded")

health_db = FakeSupabase(sources=[{"id": "s-1"}])
with mock.patch.dict(os.environ, {"CF_R2_ACCOUNT_ID": "", "ANTHROPIC_API_KEY": "",
                                  "GEMINI_API_KEY": ""}), \
     mock.patch.object(bh, "notify", return_value=SENT) as notify_health:
    stats = bh.run(supabase_client=health_db)
check("one backend_health_checks row per component",
      len(health_db.inserted.get("backend_health_checks", [])) == stats["components"] == 5)
check("healthy backend -> no email", notify_health.call_count == 0 and stats["emailed"] is False)

down_db = FakeSupabase()
down_db.table = mock.Mock(side_effect=RuntimeError("db down"))
with mock.patch.dict(os.environ, {"CF_R2_ACCOUNT_ID": ""}), \
     mock.patch.object(bh, "notify", return_value=SENT) as notify_down:
    stats = bh.run(supabase_client=down_db)
check("a down component -> exactly one email", notify_down.call_count == 1)
check("...sent as kind=health", notify_down.call_args.args[0] == "health")


# ── the hard rule: these agents may never raise ──────────────────────────────

print("\nall three — never raise:")

for name, module in (("supervisor", sup), ("maintenance", mnt), ("backend_health", bh)):
    try:
        with mock.patch.object(module, "notify", return_value=SENT):
            result = module.run(supabase_client=boom_client())
        check(f"{name}: total DB outage returns a stats dict", isinstance(result, dict))
    except Exception as exc:                      # noqa: BLE001
        check(f"{name}: total DB outage returns a stats dict (raised {exc!r})", False)

internal_failures = (
    ("supervisor", sup, "classify_findings", FakeSupabase(pipeline_state=[state("cna")])),
    ("maintenance", mnt, "group_issues", FakeSupabase()),
    ("backend_health", bh, "estimate_daily_cost", FakeSupabase(sources=[{"id": "s-1"}])),
)
for name, module, target, client in internal_failures:
    try:
        with mock.patch.object(module, target, side_effect=RuntimeError("kaboom")), \
             mock.patch.object(module, "notify", return_value=SENT):
            result = module.run(supabase_client=client)
        check(f"{name}: internal crash is caught and counted",
              result["errors"] == 1 and isinstance(result, dict))
    except Exception as exc:                      # noqa: BLE001
        check(f"{name}: internal crash is caught and counted (raised {exc!r})", False)

for name, module in (("supervisor", sup), ("maintenance", mnt), ("backend_health", bh)):
    with mock.patch.dict(os.environ, {"AGENT_DISABLED": module.AGENT}), \
         mock.patch.object(module, "notify", return_value=SENT) as notify_off:
        result = module.run(supabase_client=boom_client())
    check(f"{name}: AGENT_DISABLED kill switch skips the pass entirely",
          result.get("skipped") is True and notify_off.call_count == 0)


print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
