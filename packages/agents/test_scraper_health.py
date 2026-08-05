"""
scraper_health: the writer (ingestion/health.py) and its wiring into the live
pass (ingestion/orchestrator.py). Offline — no pytest, no DB, no network.

Run: .venv/Scripts/python.exe test_scraper_health.py

WHAT THIS FILE IS GUARDING
--------------------------
The table had a writer (`scrapers.log_scraper_run`) that lost its only caller
when ingestion moved to the source adapters, while the War Room kept reading it.
Nothing failed — the fleet was simply displayed from rows that had stopped
moving. So the tests that matter are the boring structural ones:

  1. A pass writes a row for every source it FETCHED, and for no source it
     skipped (a phantom zero-item row misreports a source as silently dead).
  2. The row is keyed by the STABLE SOURCE ID, the same key as pipeline_state,
     so one source can never render as two under different spellings.
  3. A failed fetch still writes a row, as status='error'.

NOT covered here: the supervisor's zero-streak alerting. That derives from
`pipeline_run_history` (see ops/supervisor.py and test_ops_agents.py), which is
written on every real pass. scraper_health feeds the War Room health *views*;
alerting deliberately does not depend on it.
"""
import importlib
import logging
import os
from datetime import date, datetime, timedelta, timezone
from unittest import mock

logging.disable(logging.CRITICAL)

health = importlib.import_module("ingestion.health")

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name} {detail}")


# ── classify: the pure grading rules ─────────────────────────────────────────

print("health.classify — status rules:\n")

s, reason, zeros = health.classify(items_found=4, duration_ms=800, errors=None)
check("items found -> ok", s == "ok" and reason is None)
check("items found resets the zero streak",
      health.classify(items_found=4, duration_ms=1, errors=None,
                      last_consecutive_zeros=9)[2] == 0)

check("0 items once -> ok, streak 1 (0 items is the NORMAL Yishun-filter case)",
      health.classify(items_found=0, duration_ms=1, errors=None,
                      last_consecutive_zeros=0)[::2] == ("ok", 1))
check("0 items twice -> still ok",
      health.classify(items_found=0, duration_ms=1, errors=None,
                      last_consecutive_zeros=1)[0] == "ok")

# The threshold moved from 3 to 30 on 2026-08-02. `items_found` counts
# candidates that survived the YISHUN keyword filter, not articles served, so a
# single outlet publishing nothing about one town for three days is
# unremarkable — Tamil Murasu or Berita Harian can go a month. At 3 the whole
# fleet sat permanently at `warning` (9 of 15 sources on 2026-08-02, every one
# reading "0 items for 3 consecutive runs"), which reads as a dead fleet when
# nothing has failed. Assert against the constant, not a literal, so the
# semantics are pinned but the number stays tunable.
check("a quiet source below the threshold stays ok",
      health.classify(items_found=0, duration_ms=1, errors=None,
                      last_consecutive_zeros=health.ZERO_STREAK_WARNING - 2)[0] == "ok")
sN, reasonN, zerosN = health.classify(
    items_found=0, duration_ms=1, errors=None,
    last_consecutive_zeros=health.ZERO_STREAK_WARNING - 1)
check("0 items on the ZERO_STREAK_WARNING-th consecutive run -> warning",
      sN == "warning" and zerosN == health.ZERO_STREAK_WARNING
      and f"{health.ZERO_STREAK_WARNING} consecutive" in reasonN)
check("threshold is a month of daily passes, not a few days",
      health.ZERO_STREAK_WARNING >= 14)

s_err, reason_err, _ = health.classify(items_found=0, duration_ms=50, errors=["403 Forbidden"],
                                       last_consecutive_zeros=0)
check("a fetch error -> status error", s_err == "error" and "403" in reason_err)
check("error outranks a zero streak (the error is the story)",
      health.classify(items_found=0, duration_ms=1, errors=["boom"],
                      last_consecutive_zeros=7)[0] == "error")
check("a failed run still advances the zero streak",
      health.classify(items_found=0, duration_ms=1, errors=["boom"],
                      last_consecutive_zeros=7)[2] == 8)

# A ratio alone is not a signal at small magnitudes. Every amber source in the
# War Room on 2026-08-05 was a duration warning, worst being "165ms is >3x the
# 7d avg (53ms)" — nothing was wrong with any of them, and a panel that is all
# noise is a panel the operator stops reading.
check("a fast run is NOT flagged just for being 3x its own tiny baseline",
      health.classify(items_found=2, duration_ms=165, errors=None,
                      avg_duration_7d=53)[0] == "ok")
check("...nor is a sub-second run",
      health.classify(items_found=2, duration_ms=900, errors=None,
                      avg_duration_7d=100)[0] == "ok")
check("a genuinely slow run IS still flagged",
      health.classify(items_found=2, duration_ms=60_000, errors=None,
                      avg_duration_7d=2_000)[0] == "warning")
check("slow-but-in-line-with-baseline is not flagged",
      health.classify(items_found=2, duration_ms=60_000, errors=None,
                      avg_duration_7d=40_000)[0] == "ok")
check("the absolute floor is seconds, not milliseconds",
      health.SLOW_RUN_MIN_MS >= 5_000)

# The ratio rule still applies — but only above SLOW_RUN_MIN_MS, so these use
# durations that are slow in absolute terms. They previously used 3100ms vs a
# 1000ms baseline, which stopped warning when the absolute floor was added; that
# was the intended change, not a regression.
_floor = health.SLOW_RUN_MIN_MS
check("3x the 7d baseline -> slow warning (above the absolute floor)",
      health.classify(items_found=2, duration_ms=_floor * 4, errors=None,
                      avg_duration_7d=_floor)[0] == "warning")
check("just under 3x the baseline -> ok",
      health.classify(items_found=2, duration_ms=int(_floor * 2.9), errors=None,
                      avg_duration_7d=_floor)[0] == "ok")
check("no baseline yet -> never slow-warns (first run must not be a warning)",
      health.classify(items_found=2, duration_ms=999_999, errors=None,
                      avg_duration_7d=None)[0] == "ok")


# ── record: DB shape, and never raising ──────────────────────────────────────

print("\nhealth.record — writes and failure tolerance:\n")


class FakeClient:
    """Captures inserts; serves canned rows for the two reads record() makes."""

    def __init__(self, existing=None):
        self.existing = existing or []
        self.inserted: list[dict] = []

    def table(self, name):
        client = self

        class _Q:
            def insert(self, row):
                self._row = row
                return self
            def select(self, *a, **kw):  return self
            def eq(self, *a, **kw):      return self
            def gte(self, *a, **kw):     return self
            def order(self, *a, **kw):   return self
            def limit(self, *a, **kw):   return self
            def execute(self):
                if getattr(self, "_row", None) is not None:
                    client.inserted.append(self._row)
                    return mock.Mock(data=[self._row])
                return mock.Mock(data=list(client.existing))
        return _Q()


c = FakeClient()
row = health.record("stomp", "msm", items_found=3, items_passed_s1=1,
                    duration_ms=1200, client=c)
check("record writes one row", len(c.inserted) == 1)
check("row carries the stable source id, not a display name",
      c.inserted[0]["source_name"] == "stomp")
check("row carries items_passed_s1 (the old writer could never know it)",
      c.inserted[0]["items_passed_s1"] == 1)
check("row status ok", c.inserted[0]["status"] == "ok")

c2 = FakeClient(existing=[{"consecutive_zeros": health.ZERO_STREAK_WARNING - 1,
                           "duration_ms": 1000}])
health.record("stomp", "msm", items_found=0, duration_ms=1000, client=c2)
check("zero streak continues from this source's newest row",
      c2.inserted[0]["consecutive_zeros"] == health.ZERO_STREAK_WARNING
      and c2.inserted[0]["status"] == "warning")

# The counter must keep advancing below the threshold without warning — that is
# what makes a genuinely dead source eventually surface.
c2b = FakeClient(existing=[{"consecutive_zeros": 2, "duration_ms": 1000}])
health.record("stomp", "msm", items_found=0, duration_ms=1000, client=c2b)
check("a short zero streak advances but stays ok",
      c2b.inserted[0]["consecutive_zeros"] == 3 and c2b.inserted[0]["status"] == "ok")

c3 = FakeClient()
health.record("edmw", "signal", items_found=0, duration_ms=90,
              errors=["edmw: 403 Forbidden"], client=c3)
check("a blocked source writes status=error",
      c3.inserted[0]["status"] == "error" and c3.inserted[0]["source_type"] == "signal")

boom = mock.MagicMock()
boom.table.side_effect = RuntimeError("supabase exploded")
try:
    r = health.record("cna", "msm", items_found=1, duration_ms=1, client=boom)
    ok = r is None
except Exception:
    ok = False
check("a DB outage returns None, never raises (health must not cost a pass)", ok)
check("no client -> None, no crash",
      health.record("cna", "msm", items_found=1, client=None) is None)


# ── orchestrator wiring: one row per FETCHED source, per pass ────────────────

print("\ningestion pass — health rows land on the live path:\n")

os.environ["CLUSTER_BEFORE_WRITE"] = "off"
orch = importlib.import_module("ingestion.orchestrator")
from ingestion.contracts import Candidate, SourceBlockedError  # noqa: E402


class FakeSource:
    def __init__(self, name, source_type="msm", items=1, raises=None):
        self.name = name
        self.source_type = source_type
        self.enabled = True
        self._items = items
        self._raises = raises

    def fetch(self, since):
        if self._raises:
            raise self._raises
        return [
            Candidate(title=f"Yishun thing {i}", content="c", url=f"https://x/{self.name}/{i}",
                      source_name=self.name, source_type=self.source_type,
                      published_at=date(2026, 7, 30), discovered_via=self.name)
            for i in range(self._items)
        ]


class _Budget:
    def should_halt(self):        return False
    def record(self, *a, **kw):   return None
    def mark_rpd_exhausted(self): return None


def run_pass(sources, *, dry_run=False, s1_passes=True):
    """One run_ingestion_pass with every external edge stubbed. Returns the
    scraper_health rows the pass wrote."""
    client = FakeClient()
    with mock.patch.multiple(
        orch,
        get_supabase_client=mock.Mock(return_value=client),
        load_daily_budget=mock.Mock(return_value=_Budget()),
        save_daily_budget=mock.Mock(),
        filter_content=mock.Mock(return_value={"passes": s1_passes, "usage": {}}),
        write_stage2=mock.Mock(return_value={"title": "t", "confidence": 0.5}),
        consolidation_check=mock.Mock(return_value=mock.Mock(action="skip")),
        build_queue_row=mock.Mock(return_value={}),
        check_milestones=mock.Mock(return_value={}),
        is_signal_source=mock.Mock(return_value=False),
    ), \
        mock.patch.object(orch.dedup, "is_duplicate", return_value=False), \
        mock.patch.object(orch.state_store, "get", return_value=None), \
        mock.patch.object(orch.state_store, "update"), \
        mock.patch.object(orch.state_store, "record_run"), \
        mock.patch.object(orch.learning, "load_source_reputation", return_value={}), \
        mock.patch.object(orch.learning, "load_recent_signal_patterns", return_value=None), \
        mock.patch.object(orch.fallback, "BACKOFF_SECONDS", 0):
        orch.run_ingestion_pass(sources, datetime.now(timezone.utc), dry_run=dry_run)
    return [r for r in client.inserted if "consecutive_zeros" in r]


rows = run_pass([FakeSource("cna"), FakeSource("stomp", items=2),
                 FakeSource("edmw", source_type="signal", items=0)])
by_name = {r["source_name"]: r for r in rows}
check("a pass writes one health row per source", len(rows) == 3,
      f"got {[r['source_name'] for r in rows]}")
check("rows are keyed by the stable source id (joins pipeline_state)",
      set(by_name) == {"cna", "stomp", "edmw"})
check("items_found is what the source returned", by_name["stomp"]["items_found"] == 2)
check("source_type is carried from the adapter, not guessed",
      by_name["edmw"]["source_type"] == "signal")
check("items_passed_s1 counts Stage 1 passes for that source",
      by_name["stomp"]["items_passed_s1"] == 2)
check("a source with nothing to offer is ok, not an error",
      by_name["edmw"]["status"] == "ok" and by_name["edmw"]["items_found"] == 0)
check("duration_ms is recorded", by_name["cna"]["duration_ms"] is not None)

rows_s1 = run_pass([FakeSource("cna", items=3)], s1_passes=False)
check("Stage 1 rejecting everything still records items_found (fetch worked)",
      rows_s1[0]["items_found"] == 3 and rows_s1[0]["items_passed_s1"] == 0)

rows_blocked = run_pass([FakeSource("cna"),
                         FakeSource("stomp", raises=SourceBlockedError("stomp: 403 Forbidden"))])
blocked = {r["source_name"]: r for r in rows_blocked}
check("a blocked source gets a row too — silence is the bug being fixed",
      set(blocked) == {"cna", "stomp"})
check("the blocked source's row is status=error with the reason",
      blocked["stomp"]["status"] == "error" and "403" in (blocked["stomp"]["status_reason"] or ""))
check("the healthy source in the same pass is unaffected", blocked["cna"]["status"] == "ok")

check("dry_run writes NO health rows (a dry run writes nothing at all)",
      run_pass([FakeSource("cna")], dry_run=True) == [])



print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
