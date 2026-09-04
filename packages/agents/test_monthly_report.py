"""
Self-contained tests for the monthly orchestrator report. No pytest, no DB.
Run: .venv/Scripts/python.exe test_monthly_report.py

Two things must hold or the report is worse than useless:

  1. AGGREGATION. Every section has to survive a missing table and an empty
     month WITHOUT crashing and without inventing a number — an operator who
     reads "0 published" when the query actually failed will make the wrong
     call. Unreadable renders as available=false; genuinely empty renders as 0.
  2. IDEMPOTENCE. monthly_reports is UNIQUE (period_start, period_end) and the
     alert kind is never throttled, so a re-run that generated a second row
     would also send a second alert. Running twice must write once.

AgentRun's Supabase client is patched out for the whole file: the repo has a
live .env, and an observability write from a test run would land in the real
agent_runs table.
"""
import importlib
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

import ops.activity as activity

_no_db = mock.patch.object(activity, "_client", side_effect=lambda explicit=None: explicit)
_no_db.start()

mr = importlib.import_module("ops.monthly_report")

passed = failed = 0
def check(name, cond):
    global passed, failed
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    passed += 1 if cond else 0
    failed += 0 if cond else 1


# ── fake Supabase client ────────────────────────────────────────────────────

class FakeTable:
    def __init__(self, name, db):
        self.name, self.db = name, db
        self._op = "select"
        self._filters: list[tuple] = []
        self._payload = None
        self._order = None
        self._limit = None

    def select(self, *_a, **_kw):                 self._op = "select"; return self
    def gte(self, col, val):                      self._filters.append(("gte", col, val)); return self
    def lt(self, col, val):                       self._filters.append(("lt", col, val)); return self
    def eq(self, col, val):                       self._filters.append(("eq", col, val)); return self
    def order(self, col, desc=False):             self._order = (col, desc); return self
    def limit(self, n):                           self._limit = n; return self
    def update(self, patch):                      self._op = "update"; self._payload = patch; return self
    def insert(self, rows):                       self._op = "insert"; self._payload = rows; return self

    def upsert(self, row, on_conflict=None):
        self._op = "upsert"
        self._payload = row
        self._conflict = [c.strip() for c in (on_conflict or "").split(",") if c.strip()]
        return self

    def _matches(self, row):
        for op, col, val in self._filters:
            actual = row.get(col)
            if op == "eq" and actual != val:
                return False
            if op == "gte" and (actual is None or str(actual) < str(val)):
                return False
            if op == "lt" and (actual is None or str(actual) >= str(val)):
                return False
        return True

    def execute(self):
        if self.name in self.db.broken:
            raise RuntimeError(f'relation "{self.name}" does not exist')
        self.db.calls.append((self.name, self._op))
        rows = self.db.tables.setdefault(self.name, [])

        if self._op == "select":
            hits = [r for r in rows if self._matches(r)]
            if self._order:
                col, desc = self._order
                hits.sort(key=lambda r: str(r.get(col) or ""), reverse=desc)
            if self._limit:
                hits = hits[: self._limit]
            return SimpleNamespace(data=hits)

        if self._op == "upsert":
            key = lambda r: tuple(r.get(c) for c in self._conflict)   # noqa: E731
            for i, existing in enumerate(rows):
                if key(existing) == key(self._payload):
                    rows[i] = {**existing, **self._payload}
                    return SimpleNamespace(data=[rows[i]])
            rows.append({"id": f"row-{len(rows) + 1}", **self._payload})
            return SimpleNamespace(data=[rows[-1]])

        if self._op == "update":
            for i, existing in enumerate(rows):
                if self._matches(existing):
                    rows[i] = {**existing, **self._payload}
            return SimpleNamespace(data=[])

        rows.extend(self._payload if isinstance(self._payload, list) else [self._payload])
        return SimpleNamespace(data=[])


class FakeClient:
    def __init__(self, tables=None, broken=()):
        self.tables = {k: list(v) for k, v in (tables or {}).items()}
        self.broken = set(broken)
        self.calls: list[tuple] = []

    def table(self, name):
        return FakeTable(name, self)


# ── fixtures ────────────────────────────────────────────────────────────────

END = date(2026, 7, 20)
START = END - timedelta(days=29)


def _at(day: int) -> str:
    """An ISO timestamp inside the window (day 1 = period_start)."""
    return datetime.combine(START + timedelta(days=day - 1),
                            datetime.min.time(), tzinfo=timezone.utc).isoformat()


PASSES = [
    {"ran_at": _at(2), "dry_run": False, "degraded": False, "total_queued": 3,
     "report": {"per_source": [
         {"name": "cna",   "status": "ok",      "fetched": 10, "fresh": 4, "novel": 3, "queued": 2},
         {"name": "stomp", "status": "blocked", "fetched": 0,  "fresh": 0, "novel": 0, "queued": 0,
          "reason": "403 bot-detection"},
     ]}},
    {"ran_at": _at(9), "dry_run": False, "degraded": True, "total_queued": 1,
     "report": {"per_source": [
         {"name": "cna", "status": "ok", "fetched": 6, "fresh": 2, "novel": 1, "queued": 1},
     ]}},
    # dry runs write nothing downstream, so they must not inflate the totals
    {"ran_at": _at(9), "dry_run": True, "degraded": False, "total_queued": 99,
     "report": {"per_source": [{"name": "cna", "status": "ok", "fetched": 99, "queued": 99}]}},
]

INCIDENTS = [
    {"id": "i-1", "title": "A", "slug": "a", "classification": "dagger", "severity": 4, "published_at": _at(3)},
    {"id": "i-2", "title": "B", "slug": "b", "classification": "clown",  "severity": 2, "published_at": _at(5)},
    {"id": "i-3", "title": "C", "slug": "c", "classification": "dagger", "severity": 3, "published_at": _at(11)},
]

SIGNALS = [
    {"action": "auto_approve", "decided_by": "agent",    "incident_id": "i-1", "created_at": _at(3)},
    {"action": "approve",      "decided_by": "operator", "incident_id": "i-2", "created_at": _at(5)},
    {"action": "edit_approve", "decided_by": "operator", "incident_id": "i-3", "created_at": _at(11)},
    {"action": "reject",       "decided_by": "operator", "incident_id": None,  "created_at": _at(12)},
    {"action": "auto_publish_reverted", "decided_by": "operator", "incident_id": "i-1", "created_at": _at(14)},
]

SNAPSHOTS = [
    {"captured_at": _at(28), "sample_count": 40, "mean_confidence": 0.81, "confidence_delta": 0.02,
     "agreement_rate": 0.72, "agreement_delta": 0.05, "edit_rate": 0.2, "reject_rate": 0.08,
     "auto_publish_count": 1, "auto_publish_reverted": 1, "verdict": "learning"},
    {"captured_at": _at(4), "sample_count": 22, "mean_confidence": 0.77, "confidence_delta": 0.01,
     "agreement_rate": 0.66, "agreement_delta": 0.01, "edit_rate": 0.3, "reject_rate": 0.1,
     "auto_publish_count": 0, "auto_publish_reverted": 0, "verdict": "stagnant"},
    # the month-over-month baseline — captured BEFORE the window opens
    {"captured_at": _at(-40), "sample_count": 12, "agreement_rate": 0.60, "verdict": "stagnant"},
]

RUNS = [
    {"agent": "auto_publish", "status": "ok",       "duration_ms": 100, "started_at": _at(2)},
    {"agent": "auto_publish", "status": "degraded", "duration_ms": 300, "started_at": _at(3)},
    {"agent": "supervisor",   "status": "failed",   "duration_ms": None, "started_at": _at(4)},
    {"agent": "supervisor",   "status": "running",  "duration_ms": None, "started_at": _at(5)},
]

EVENTS = [
    {"level": "error",   "event": "source_blocked", "agent": "supervisor", "created_at": _at(2)},
    {"level": "error",   "event": "source_blocked", "agent": "supervisor", "created_at": _at(3)},
    {"level": "anomaly", "event": "zero_streak",    "agent": "supervisor", "created_at": _at(4)},
    {"level": "info",    "event": "pass_started",   "agent": "supervisor", "created_at": _at(5)},
]

CHECKS = [
    {"component": "supabase",      "status": "ok",       "message": None, "detail": {}, "checked_at": _at(2)},
    {"component": "cloudflare_r2", "status": "degraded", "message": "slow", "detail": {}, "checked_at": _at(3)},
    {"component": "cloudflare_r2", "status": "ok",       "message": None, "detail": {}, "checked_at": _at(9)},
    {"component": "cost_guard",    "status": "ok",       "message": "US$12.40 of US$50 budget",
     "detail": {"spend_usd": 12.4}, "checked_at": _at(20)},
]

NOTIFICATIONS = [
    {"kind": "review_queue", "status": "sent",       "created_at": _at(2)},
    {"kind": "review_queue", "status": "suppressed", "created_at": _at(3)},
    {"kind": "health",       "status": "sent",       "created_at": _at(4)},
]


def full_db():
    return {
        "pipeline_run_history":  PASSES,
        "incidents":             INCIDENTS,
        "training_signals":      SIGNALS,
        "learning_snapshots":    SNAPSHOTS,
        "agent_runs":            RUNS,
        "agent_events":          EVENTS,
        "backend_health_checks": CHECKS,
        "notifications":         NOTIFICATIONS,
        "monthly_reports":       [],
    }


print("monthly report tests:")

# ── window ──────────────────────────────────────────────────────────────────

s, e = mr.window_for(END)
check("window is 30 days inclusive", (e - s).days == 29 and e == END)
check("period_end accepts an ISO string", mr.window_for("2026-07-20") == (START, END))
check("default window ends yesterday IN SGT, not UTC (the same-1st double-send "
      "this fixed: the 02:58/14:58 SGT passes straddle UTC midnight)",
      mr.window_for()[1] == datetime.now(timezone.utc).astimezone(mr.SGT).date() - timedelta(days=1))

# ── (regression) the exact live incident: two SGT passes on the 1st straddling
#    UTC midnight must compute the SAME window, or the DB unique constraint and
#    notify() dedup both get defeated by a one-day-shifted period_end. ────────
_02_58_sgt = datetime(2026, 9, 1, 2, 58, tzinfo=mr.SGT)   # = 2026-08-31 18:58 UTC
_14_58_sgt = datetime(2026, 9, 1, 14, 58, tzinfo=mr.SGT)  # = 2026-09-01 06:58 UTC
check("the two SGT instants really do straddle a UTC calendar day",
      _02_58_sgt.astimezone(timezone.utc).date() != _14_58_sgt.astimezone(timezone.utc).date())
check("...but window_for() agrees on period_end for both (the fix)",
      mr.window_for(now=_02_58_sgt) == mr.window_for(now=_14_58_sgt)
      == (date(2026, 8, 2), date(2026, 8, 31)))
lo, hi = mr._bounds(START, END)
check("upper bound is exclusive midnight after period_end", hi.startswith("2026-07-21T00:00:00"))
check("lower bound is midnight on period_start", lo.startswith("2026-06-21T00:00:00"))

# ── ingestion ───────────────────────────────────────────────────────────────

ing = mr.summarise_ingestion(PASSES)
check("ingestion counts only live passes", ing["passes"] == 2)
check("ingestion sums total_queued, ignoring dry runs", ing["total_queued"] == 4)
check("ingestion counts degraded passes", ing["degraded_passes"] == 1)
check("ingestion degraded_rate", ing["degraded_rate"] == 0.5)
cna = next(s for s in ing["per_source"] if s["source"] == "cna")
check("per-source fetched summed", cna["fetched"] == 16)
check("per-source queued summed", cna["queued"] == 3)
stomp = next(s for s in ing["per_source"] if s["source"] == "stomp")
check("per-source blocked counted", stomp["blocked"] == 1)
check("per-source keeps the block reason", stomp["last_reason"] == "403 bot-detection")
check("blocked sources listed", ing["sources_blocked"] == ["stomp"])
check("per_source sorted by queued desc", ing["per_source"][0]["source"] == "cna")
check("unreadable table -> available=false", mr.summarise_ingestion(None)["available"] is False)
check("empty month -> available=true, zero passes",
      mr.summarise_ingestion([])["available"] is True and mr.summarise_ingestion([])["passes"] == 0)
check("junk report JSONB does not crash",
      mr.summarise_ingestion([{"dry_run": False, "total_queued": None,
                               "report": "not-json"}])["total_queued"] == 0)

# ── publishing ──────────────────────────────────────────────────────────────

pub = mr.summarise_publishing(INCIDENTS, SIGNALS)
check("published count", pub["published"] == 3)
check("auto vs operator split", pub["auto_published"] == 1 and pub["operator_approved"] == 2)
check("auto share", pub["auto_share"] == 0.333)
check("by_classification", pub["by_classification"] == {"dagger": 2, "clown": 1})
check("by_severity keyed by string", pub["by_severity"] == {"2": 1, "3": 1, "4": 1})
check("mean severity", pub["mean_severity"] == 3.0)
check("recent list is newest-first", [i["slug"] for i in pub["recent"]] == ["c", "b", "a"])
check("recent flags the autonomous one", pub["recent"][2]["auto"] is True)
check("signals unreadable -> split_available=false",
      mr.summarise_publishing(INCIDENTS, None)["split_available"] is False)
check("incidents unreadable -> available=false", mr.summarise_publishing(None, SIGNALS)["available"] is False)
check("empty month publishes nothing without crashing",
      mr.summarise_publishing([], [])["published"] == 0
      and mr.summarise_publishing([], [])["mean_severity"] is None)

# ── operator workload (the review-saved number) ─────────────────────────────

op = mr.summarise_operator(SIGNALS)
check("operator decisions exclude agent decisions", op["operator_decisions"] == 3)
check("approve/edit/reject broken out",
      (op["approve"], op["edit_approve"], op["reject"]) == (1, 1, 1))
check("agent decisions counted", op["agent_decisions"] == 1)
check("reverted auto-publish counted", op["reverted"] == 1)
check("reviews saved is gross", op["reviews_saved"] == 1)
check("net reviews saved subtracts reverts", op["net_reviews_saved"] == 0)
check("minutes saved follows net, not gross", op["minutes_saved"] == 0)
check("autonomy share", op["autonomy_share"] == 0.25)
op2 = mr.summarise_operator([
    {"action": "auto_approve", "decided_by": "agent"},
    {"action": "auto_approve", "decided_by": "agent"},
    {"action": "approve",      "decided_by": "operator"},
])
check("clean autonomy month saves review time",
      op2["net_reviews_saved"] == 2 and op2["minutes_saved"] == 2 * mr.MINUTES_PER_REVIEW)
check("legacy rows without decided_by still read as agent via action",
      mr.summarise_operator([{"action": "auto_approve"}])["agent_decisions"] == 1)
check("operator table unreadable -> available=false", mr.summarise_operator(None)["available"] is False)
check("empty month -> zero decisions, no divide-by-zero",
      mr.summarise_operator([])["operator_decisions"] == 0
      and mr.summarise_operator([])["autonomy_share"] is None)

# ── learning ────────────────────────────────────────────────────────────────

baseline = [SNAPSHOTS[2]]
learn = mr.summarise_learning(SNAPSHOTS[:2], baseline)
check("latest in-window snapshot wins", learn["agreement_rate"] == 0.72)
check("verdict carried through", learn["verdict"] == "learning")
check("month-over-month agreement delta", learn["agreement_vs_previous_month"] == 0.12)
check("previous month recorded", learn["previous"]["agreement_rate"] == 0.6)
check("no baseline -> no cross-month delta",
      mr.summarise_learning(SNAPSHOTS[:2], [])["agreement_vs_previous_month"] is None)
check("no snapshot this period -> captured=false",
      mr.summarise_learning([], baseline)["captured"] is False)
check("learning table unreadable -> available=false", mr.summarise_learning(None, None)["available"] is False)
check("decimal-as-string tolerated",
      mr.summarise_learning([{"captured_at": _at(2), "agreement_rate": "0.5",
                              "verdict": "learning"}], [])["agreement_rate"] == 0.5)

# ── reliability ─────────────────────────────────────────────────────────────

rel = mr.summarise_reliability(RUNS, EVENTS)
check("run totals", (rel["runs"], rel["ok"], rel["degraded"], rel["failed"], rel["running"]) == (4, 1, 1, 1, 1))
ap = next(a for a in rel["by_agent"] if a["agent"] == "auto_publish")
check("per-agent avg duration", ap["avg_duration_ms"] == 200)
check("failing agents sort first", rel["by_agent"][0]["agent"] == "supervisor")
check("error/anomaly counted, info ignored", rel["events"] == {"error": 2, "anomaly": 1})
check("top events ranked by count", rel["top_events"][0] == {"event": "source_blocked", "level": "error", "count": 2})
check("top events capped at 10",
      len(mr.summarise_reliability([], [{"level": "error", "event": f"e{i}"} for i in range(20)])["top_events"]) == 10)
check("both tables unreadable -> available=false", mr.summarise_reliability(None, None)["available"] is False)
check("runs unreadable but events readable still reports",
      mr.summarise_reliability(None, EVENTS)["runs_readable"] is False)

# ── health + cost ───────────────────────────────────────────────────────────

hl = mr.summarise_health(CHECKS)
r2 = next(c for c in hl["components"] if c["component"] == "cloudflare_r2")
check("worst status per component is sticky", r2["worst_status"] == "degraded")
check("last status is the most recent, not the worst", r2["last_status"] == "ok")
check("overall worst status", hl["worst_status"] == "degraded")
check("worst component sorts first", hl["components"][0]["component"] == "cloudflare_r2")
check("cost guard picked out", hl["cost_guard"]["detail"]["spend_usd"] == 12.4)
check("health unreadable -> available=false", mr.summarise_health(None)["available"] is False)
check("empty month -> no components, no crash",
      mr.summarise_health([])["components"] == [] and mr.summarise_health([])["worst_status"] is None)

# ── notifications ───────────────────────────────────────────────────────────

nt = mr.summarise_notifications(NOTIFICATIONS)
check("notifications sent counted", nt["sent"] == 2)
rq = next(k for k in nt["by_kind"] if k["kind"] == "review_queue")
check("suppressed tracked separately", (rq["sent"], rq["suppressed"]) == (1, 1))
check("notifications unreadable -> available=false", mr.summarise_notifications(None)["available"] is False)

# ── collect(): windowing, previous period, changes ──────────────────────────

client = FakeClient(full_db())
report = mr.collect(client, START, END, trigger="manual")
check("collect stamps the period", report["period"]["start"] == "2026-06-21")
check("collect stamps the trigger", report["period"]["trigger"] == "manual")
check("collect aggregates publishing", report["publishing"]["published"] == 3)
check("collect excludes the pre-window snapshot from the in-window set",
      report["learning"]["snapshots"] == 2)
check("collect uses the pre-window snapshot as the baseline",
      report["learning"]["previous"]["agreement_rate"] == 0.6)
check("previous period is the 30 days before the window",
      report["previous_period"]["start"] == "2026-05-22" and report["previous_period"]["end"] == "2026-06-20")
check("empty previous period -> zeros, not None",
      report["previous_period"]["published"] == 0)
check("changes computed vs previous period", report["changes"]["published"] == 3)
check("no data gaps on a healthy DB", report["warnings"] == [])

broken = FakeClient(full_db(), broken=["agent_runs", "agent_events", "backend_health_checks"])
degraded = mr.collect(broken, START, END)
check("a missing table degrades only its own section",
      degraded["reliability"]["available"] is False and degraded["publishing"]["published"] == 3)
check("missing tables are surfaced as warnings", len(degraded["warnings"]) >= 3)

empty = mr.collect(FakeClient({}), START, END)
check("an empty month produces a valid report", empty["publishing"]["published"] == 0)
check("an empty month has no NaN in the shares",
      empty["publishing"]["auto_share"] is None and empty["ingestion"]["degraded_rate"] is None)

# ── narrative ───────────────────────────────────────────────────────────────

text = mr.build_summary_text(report)
check("narrative leads with the period", text.splitlines()[0] == "YISHUN AGAIN — MONTHLY REPORT")
check("narrative leads with what changed", "WHAT CHANGED" in text.splitlines()[3])
check("narrative states the delta, not just the total", "vs 0 in the previous 30 days" in text)
check("narrative reports review avoided", "Review avoided on 1 card(s)" in text)
check("narrative reports the learning verdict", "Verdict: LEARNING" in text)
check("narrative names blocked sources", "stomp" in text)
check("narrative reports the cost guard", "US$12.40" in text)
check("narrative links the War Room", "/reports" in text)
empty_text = mr.build_summary_text(empty)
check("empty month still produces a readable narrative",
      "Nothing was published this period." in empty_text)
check("unreadable sections say so in the narrative",
      "Unavailable (" in mr.build_summary_text(degraded))

# ── run(): storage, idempotence, alert ───────────────────────────────────────

def run_with(client, **kw):
    """run() with the notifier stubbed — the Telegram transport is notify.py's test."""
    sent = []
    def fake_notify(kind, subject, body, **kwargs):
        sent.append({"kind": kind, "subject": subject, "body": body, **kwargs})
        return {"status": "sent", "id": "n-1", "error": None}
    with mock.patch.object(mr, "notify", side_effect=fake_notify):
        stats = mr.run(supabase_client=client, period_end=END, **kw)
    return stats, sent


client = FakeClient(full_db())
stats, sent = run_with(client)
rows = client.tables["monthly_reports"]
check("run reports ok", stats["status"] == "ok")
check("run writes exactly one row", len(rows) == 1)
check("stored row carries the period", (rows[0]["period_start"], rows[0]["period_end"]) == ("2026-06-21", "2026-07-20"))
check("stored report is the JSONB dict", isinstance(rows[0]["report"], dict))
check("stored summary_text is the narrative", rows[0]["summary_text"].startswith("YISHUN AGAIN"))
check("run alerts the report", len(sent) == 1 and sent[0]["kind"] == "monthly_report")
check("alert is deduped on the period", sent[0]["dedup_key"] == "monthly_report:2026-06-21:2026-07-20")
check("emailed_at stamped after a successful send", rows[0]["emailed_at"] is not None)
check("run surfaces the headline numbers", (stats["published"], stats["auto_published"]) == (3, 1))

stats2, sent2 = run_with(client)
check("second run does not create a second row", len(client.tables["monthly_reports"]) == 1)
check("second run reports 'exists'", stats2["status"] == "exists")
check("second run does not re-alert", sent2 == [])

check("first run recorded its trigger",
      client.tables["monthly_reports"][0]["report"]["period"]["trigger"] == "scheduler")
stats3, sent3 = run_with(client, force=True, trigger="manual")
check("force=True still writes only one row", len(client.tables["monthly_reports"]) == 1)
check("force=True regenerates and overwrites the stored report",
      client.tables["monthly_reports"][0]["report"]["period"]["trigger"] == "manual")
check("force=True re-alerts", len(sent3) == 1)

# the upsert itself must be the duplicate guard, even if the pre-check is blind
guarded = FakeClient(full_db())
guarded.tables["monthly_reports"] = [
    {"id": "row-1", "period_start": "2026-06-21", "period_end": "2026-07-20",
     "report": {}, "summary_text": "stale", "emailed_at": None}
]
with mock.patch.object(mr, "_existing", return_value=None):
    run_with(guarded)
check("upsert on (period_start, period_end) overwrites rather than duplicating",
      len(guarded.tables["monthly_reports"]) == 1
      and guarded.tables["monthly_reports"][0]["summary_text"] != "stale")

# ── run() never raises ──────────────────────────────────────────────────────

dead = FakeClient(full_db(), broken=["monthly_reports"])
stats4, sent4 = run_with(dead)
check("a dead monthly_reports table fails soft", stats4["status"] == "failed" and stats4["errors"] == 1)
check("the report is still emailed when storage fails", len(sent4) == 1)

all_dead = FakeClient({}, broken=[
    "pipeline_run_history", "incidents", "training_signals", "learning_snapshots",
    "agent_runs", "agent_events", "backend_health_checks", "notifications",
])
stats5, _ = run_with(all_dead)
check("every table missing still returns a stats dict", stats5["status"] == "ok" and stats5["written"] is True)
check("every table missing is reported as data gaps", len(stats5["warnings"]) >= 8)

exploding = mock.MagicMock()
exploding.table.side_effect = RuntimeError("connection reset")
stats6, _ = run_with(exploding)
check("a client that explodes on every call never raises to the caller", stats6["status"] in ("ok", "failed"))

with mock.patch.object(mr, "agent_enabled", return_value=False):
    off = mr.run(supabase_client=FakeClient(full_db()), period_end=END)
check("AGENT_DISABLED short-circuits", off["status"] == "disabled")

# _client is patched rather than passing None: the repo has a live .env, and an
# unpatched fallback would build a real client against the production DB.
with mock.patch.object(mr, "_client", side_effect=EnvironmentError("SUPABASE_URL not set")):
    no_client, _ = run_with(FakeClient(full_db()))
check("no Supabase client -> failed, not an exception", no_client["status"] == "failed")

_no_db.stop()
print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
