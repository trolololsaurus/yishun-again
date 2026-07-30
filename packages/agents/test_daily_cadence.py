"""
Self-contained tests for the daily chain's cadence gates. No pytest, no DB, no
network, no optional dependencies.
Run: .venv/Scripts/python.exe test_daily_cadence.py

Four agents — pattern detection, recalibration, lifecycle and source discovery —
spent their whole existence registered only on the in-process APScheduler, which
production never starts. They had never run once. They are now cadence-gated
steps in ops/daily.py, and this file pins the three things that would let that
happen again or make it worse:

  1. THE GATES THEMSELVES. `cadence_plan` is pure, so "does lifecycle fire on a
     Tuesday" is a unit test rather than a thing you find out in a month.
  2. RECALIBRATION RUNS BEFORE INGESTION. It writes calibration hints that this
     pass's Stage 2 reads from a disk that does not survive to the next pass.
     Grouped with the other monitors — the obvious-looking place — the hints are
     written after every reader has finished and destroyed before the next one
     starts, and the loop is a silent no-op again.
  3. A DRY RUN WRITES NOTHING. None of these agents has a read-only mode, so
     `?dry_run=true` must not call them at all.

Plus the rule the whole chain rests on: one step failing must not cost the rest.
"""
import importlib
import logging
import sys
import types
from datetime import date, datetime, timezone
from unittest import mock

logging.disable(logging.CRITICAL)

daily = importlib.import_module("ops.daily")
from ingestion.contracts import IngestionReport   # noqa: E402  (stdlib-only dataclasses)

passed = failed = 0
def check(name, cond):
    global passed, failed
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    passed += 1 if cond else 0
    failed += 0 if cond else 1


# 2026-08-03 is a Monday and the first Monday of August.
FIRST_MONDAY = date(2026, 8, 3)
LATER_MONDAY = date(2026, 8, 10)
TUESDAY      = date(2026, 8, 4)
THE_FIRST    = date(2026, 8, 1)          # a Saturday


def plan(day, **kw):
    kw.setdefault("lifecycle_enabled", False)
    kw.setdefault("force_monthly", False)
    return daily.cadence_plan(day, **kw)


# ── cadence_plan: daily steps ────────────────────────────────────────────────

print("cadence_plan — daily steps:")

for day in (FIRST_MONDAY, TUESDAY, THE_FIRST):
    p = plan(day)
    check(f"pattern detection is due on {day} ({daily._WEEKDAYS[day.weekday()]})",
          p["pattern_detection"] is None)
    check(f"recalibration is due on {day}", p["recalibration"] is None)


# ── cadence_plan: lifecycle, the one that edits published content ────────────

print("\ncadence_plan — lifecycle (opt-in, Mondays):")

check("OFF by default even on a Monday — auto-conclude edits live incidents",
      plan(FIRST_MONDAY)["lifecycle"] is not None)
check("...and the reason names the switch, not the day",
      "LIFECYCLE_AUTO_CONCLUDE" in plan(FIRST_MONDAY)["lifecycle"])
check("enabled + Monday -> due", plan(LATER_MONDAY, lifecycle_enabled=True)["lifecycle"] is None)
check("enabled + Tuesday -> not due",
      plan(TUESDAY, lifecycle_enabled=True)["lifecycle"] == "not Monday (today is Tuesday)")

with mock.patch.dict("os.environ", {"LIFECYCLE_AUTO_CONCLUDE": "true"}):
    check("the switch is read from the environment when not passed explicitly",
          daily.cadence_plan(LATER_MONDAY)["lifecycle"] is None)
with mock.patch.dict("os.environ", {"LIFECYCLE_AUTO_CONCLUDE": "yes"}):
    check("...and accepts the usual truthy spellings",
          daily.cadence_plan(LATER_MONDAY)["lifecycle"] is None)
with mock.patch.dict("os.environ", {"LIFECYCLE_AUTO_CONCLUDE": "banana"}):
    check("...but an unrecognised value stays OFF, never on",
          daily.cadence_plan(LATER_MONDAY)["lifecycle"] is not None)


# ── cadence_plan: monthly steps ──────────────────────────────────────────────

print("\ncadence_plan — monthly steps:")

check("source discovery fires on the first Monday",
      plan(FIRST_MONDAY)["source_discovery"] is None)
check("...and not on the second", plan(LATER_MONDAY)["source_discovery"] is not None)
check("...and not on a Tuesday", plan(TUESDAY)["source_discovery"] is not None)
check("is_first_monday matches the old day='1-7', day_of_week='mon' cron",
      [d for d in range(1, 15)
       if daily.is_first_monday(date(2026, 8, d))] == [3])

check("monthly report fires on the 1st", plan(THE_FIRST)["monthly_report"] is None)
check("...and not on the 4th", plan(TUESDAY)["monthly_report"] == "not the 1st (today is the 4)")
check("FORCE_MONTHLY_REPORT overrides the date",
      plan(TUESDAY, force_monthly=True)["monthly_report"] is None)


# ── cadence_plan: a dry run must reach none of them ──────────────────────────

print("\ncadence_plan — dry run:")

dry = plan(FIRST_MONDAY, dry_run=True, lifecycle_enabled=True, force_monthly=True)
check("every cadence step is skipped on a dry run",
      all(dry[step] is not None for step in daily.CADENCE_STEPS))
check("...and says why, in terms of the agent's own limitation",
      all("read-only" in dry[step] for step in daily.CADENCE_STEPS))
check("the skip list covers every step cadence_plan decides",
      set(daily.CADENCE_STEPS) == set(dry))


# ── sgt_today: the schedule is written in SGT, so the gates must be too ──────

print("\nsgt_today:")

check("14:58 SGT (06:58 UTC) is the same calendar day either way",
      daily.sgt_today(datetime(2026, 8, 3, 6, 58, tzinfo=timezone.utc)) == FIRST_MONDAY)
check("00:30 SGT Monday is still SUNDAY in UTC — the gate must not miss it",
      daily.sgt_today(datetime(2026, 8, 2, 16, 30, tzinfo=timezone.utc)) == FIRST_MONDAY)
check("23:30 SGT Monday is still Monday",
      daily.sgt_today(datetime(2026, 8, 3, 15, 30, tzinfo=timezone.utc)) == FIRST_MONDAY)
check("SGT is UTC+8 with no DST, all year",
      daily.sgt_today(datetime(2026, 1, 5, 16, 30, tzinfo=timezone.utc)) == date(2026, 1, 6))


# ── The whole chain, with every step faked ──────────────────────────────────
# daily.run() imports each agent inside its own step function, so replacing the
# sys.modules entry is enough to run the entire chain offline — no DB, no API
# keys, and no need for anthropic/feedparser/supabase to be installed.

REPORT = IngestionReport(
    started_at=datetime(2026, 8, 3, tzinfo=timezone.utc),
    finished_at=datetime(2026, 8, 3, tzinfo=timezone.utc),
    dry_run=False, per_source=[], total_queued=3, new_count=3,
    update_count=0, phenomenon_count=0, degraded=False, infra_error=None,
)

_STEP_MODULES = {
    "classifiers.recalibration":   {"check": {"recalibrated": False}},
    "ingestion.orchestrator":      {"run_ingestion_pass": REPORT},
    "ingestion.sources":           {"get_enabled_sources": []},
    "ops.auto_publish":            {"run": {"published": 2, "needs_review": 1}},
    "ops.integrity":               {"run": {"findings": 0}},
    "ops.supervisor":              {"run": {"anomalies": 0}},
    "ops.learning_monitor":        {"run": {"verdict": "stagnant"}},
    "ops.backend_health":          {"run": {"components": 5}},
    "classifiers.pattern_detection": {"run": {"alerts_created": 2}},
    "classifiers.lifecycle":       {"run": {"concluded": 1, "errors": 0}},
    "scrapers.scrape_discovery":   {"run": {"found": 0, "inserted": 0}},
    "ops.maintenance":             {"run": {"issues": 0}},
    "ops.monthly_report":          {"run": {"written": True}},
}


class Chain:
    """Runs daily.run() with every agent stubbed. Records the call order."""

    def __init__(self, **raises):
        self.calls: list[str] = []
        self._raises = raises          # step name -> exception to raise
        self._saved: dict = {}

    def _stub(self, step, result):
        def fn(*args, **kwargs):
            self.calls.append(step)
            if step in self._raises:
                raise self._raises[step]
            return result
        return fn

    def __enter__(self):
        for mod_name, attrs in _STEP_MODULES.items():
            self._saved[mod_name] = sys.modules.get(mod_name)
            module = types.ModuleType(mod_name)
            for attr, result in attrs.items():
                step = mod_name.rsplit(".", 1)[1]
                setattr(module, attr, self._stub(step, result))
            sys.modules[mod_name] = module
        return self

    def __exit__(self, *exc):
        for mod_name, original in self._saved.items():
            if original is None:
                sys.modules.pop(mod_name, None)
            else:
                sys.modules[mod_name] = original
        return False


def run_chain(day=FIRST_MONDAY, dry_run=False, auto_conclude="true", **raises):
    # `auto_conclude`, not `lifecycle`: **raises is keyed by step name, and a
    # parameter called `lifecycle` swallows the lifecycle failure case silently.
    with Chain(**raises) as chain, \
         mock.patch.object(daily, "sgt_today", return_value=day), \
         mock.patch.dict("os.environ", {"LIFECYCLE_AUTO_CONCLUDE": auto_conclude,
                                        "AGENT_DISABLED": ""}):
        report = daily.run(dry_run=dry_run, trigger="manual")
    return report, chain.calls


print("\ndaily.run() — the chain:")

report, calls = run_chain()
steps = report["steps"]
check("nothing failed on a first Monday with lifecycle enabled",
      not [k for k, v in steps.items() if v.get("error")])
check("...and the only step skipped is the monthly report (Aug 3 is not the 1st)",
      [k for k, v in steps.items() if v.get("skipped")] == ["monthly_report"])
check("recalibration runs BEFORE ingestion — it writes the hints ingestion reads",
      calls.index("recalibration") < calls.index("orchestrator"))
check("auto-publish runs after ingestion",
      calls.index("orchestrator") < calls.index("auto_publish"))
check("integrity audits what went live, so it runs after auto-publish",
      calls.index("auto_publish") < calls.index("integrity"))
check("pattern detection scans after publish, so today's incidents are in the pool",
      calls.index("auto_publish") < calls.index("pattern_detection"))
check("maintenance is last of the reporting steps — it reads what the others logged",
      calls.index("maintenance") > max(calls.index(s) for s in
                                       ("integrity", "supervisor", "backend_health",
                                        "pattern_detection", "lifecycle")))
check("the cadence decision is recorded on the report",
      report["cadence_date_sgt"] == FIRST_MONDAY.isoformat())
check("the summary surfaces cadence outcomes, not just the queue counts",
      "2 pattern alert(s)" in report["summary"] and "1 auto-concluded" in report["summary"])


# ── the chain on an ordinary day ────────────────────────────────────────────

report, calls = run_chain(day=TUESDAY)
check("on a Tuesday, lifecycle and discovery are skipped with a reason",
      steps_skipped := all(report["steps"][s].get("skipped")
                           for s in ("lifecycle", "source_discovery")))
check("...and neither agent was actually called",
      "lifecycle" not in calls and "scrape_discovery" not in calls)
check("...while the daily pair still ran",
      "pattern_detection" in calls and "recalibration" in calls)
check("a skipped step is not counted as a failure", report["failed_steps"] == [])


# ── dry run ─────────────────────────────────────────────────────────────────

print("\ndaily.run() — dry run:")

report, calls = run_chain(dry_run=True)
for step in daily.CADENCE_STEPS:
    check(f"dry run does not call {step}",
          report["steps"][step].get("skipped") is not None)
check("no cadence agent was invoked at all on a dry run",
      not {"recalibration", "pattern_detection", "lifecycle",
           "scrape_discovery", "monthly_report"} & set(calls))
check("...but ingestion and the monitors still run, which is the point of a dry run",
      "orchestrator" in calls and "supervisor" in calls)


# ── failure isolation ───────────────────────────────────────────────────────

print("\ndaily.run() — failure isolation:")

report, calls = run_chain(pattern_detection=RuntimeError("entity extraction exploded"))
check("a crashing cadence step is recorded, not raised",
      "entity extraction exploded" in report["steps"]["pattern_detection"]["error"])
check("...with a traceback for the operator",
      report["steps"]["pattern_detection"]["traceback"])
check("...and the chain continues past it to the end", "maintenance" in calls)
check("...and the run is marked degraded, naming the step",
      report["failed_steps"] == ["pattern_detection"])

report, calls = run_chain(recalibration=RuntimeError("no training signals table"))
check("recalibration failing first does not stop ingestion",
      "orchestrator" in calls and report["steps"]["ingestion"].get("error") is None)


# ── the kill switch reaches the new steps too ───────────────────────────────

print("\ndaily.run() — AGENT_DISABLED:")

with Chain() as chain, \
     mock.patch.object(daily, "sgt_today", return_value=FIRST_MONDAY), \
     mock.patch.dict("os.environ", {"LIFECYCLE_AUTO_CONCLUDE": "true",
                                    "AGENT_DISABLED": "lifecycle,pattern_detection"}):
    report = daily.run(trigger="manual")
check("AGENT_DISABLED turns off a cadence step without a redeploy",
      "AGENT_DISABLED" in report["steps"]["lifecycle"]["skipped"]
      and "AGENT_DISABLED" in report["steps"]["pattern_detection"]["skipped"])
check("...and neither was called",
      "lifecycle" not in chain.calls and "pattern_detection" not in chain.calls)
check("...while the rest of the chain is untouched", "maintenance" in chain.calls)


# ── never raises ────────────────────────────────────────────────────────────

print("\ndaily.run() — never raises:")

try:
    report, _ = run_chain(**{name: RuntimeError("everything is on fire")
                             for name in ("recalibration", "orchestrator", "auto_publish",
                                          "integrity", "supervisor", "learning_monitor",
                                          "backend_health", "pattern_detection",
                                          "lifecycle", "scrape_discovery", "maintenance")})
    check("every single step failing still returns a report", isinstance(report, dict))
    check("...listing all of them", len(report["failed_steps"]) == 11)
except Exception as exc:                          # noqa: BLE001
    check(f"every single step failing still returns a report (raised {exc!r})", False)


# ── source discovery writes to the allowlist table, so pin what it writes ────
# The DB write used to live in main.py's scheduler wrapper — i.e. in the half of
# the codebase production does not execute. Now that it is on the daily path,
# the shape of the row it inserts is a security property: `sources` is the table
# classifiers/source_allowlist.py consults to decide whether a URL may be cited.

print("\nscrape_discovery.run() — what lands in `sources`:")

import scrapers.scrape_discovery as discovery          # noqa: E402

CANDIDATE = {"name": "newsite.sg", "url": "https://newsite.sg",
             "type": "msm", "notes": "Discovered via Google News"}


class RecordingClient:
    def __init__(self, explode=False):
        self.rows: list[dict] = []
        self._explode = explode

    def table(self, name):
        assert name == "sources", f"discovery wrote to {name}"
        return self

    def insert(self, payload):
        if self._explode:
            raise RuntimeError("duplicate key value violates unique constraint")
        self.rows.append(payload)
        return self

    def execute(self):
        return mock.Mock(data=[])


client = RecordingClient()
with mock.patch.object(discovery, "discover", return_value=[CANDIDATE]):
    stats = discovery.run(supabase_client=client)
row = client.rows[0]

check("a discovered candidate is filed", stats == {"found": 1, "inserted": 1,
                                                   "skipped": 0, "errors": 0})
check("it is NOT approved — nothing may cite it until the operator says so",
      row["approved_by_operator"] is False)
check("it is NOT active — is_active defaults to TRUE in the schema, so an "
      "unvetted domain would otherwise be filed looking like a live source",
      row["is_active"] is False)
check("...and carries no scrape interval", row["scrape_interval_minutes"] == 0)
check("the discovery note is kept for the operator's review", row["discovery_notes"])

dupe = RecordingClient(explode=True)
with mock.patch.object(discovery, "discover", return_value=[CANDIDATE]):
    stats = discovery.run(supabase_client=dupe)
check("a domain we already know is a skip, not an error (name is UNIQUE)",
      stats["skipped"] == 1 and stats["errors"] == 0)

with mock.patch.object(discovery, "discover", side_effect=RuntimeError("feed down")):
    stats = discovery.run(supabase_client=RecordingClient())
check("a dead feed is counted, never raised into the daily chain",
      stats["errors"] == 1 and stats["found"] == 0)

with mock.patch.object(discovery, "discover", return_value=[]):
    stats = discovery.run(supabase_client=RecordingClient())
check("no candidates -> no client work at all", stats["found"] == 0)


print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
