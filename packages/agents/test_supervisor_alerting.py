"""
Self-contained tests for two ops/supervisor.py bugs, both follow-ups to the
discovery-tier zero-streak fix (see git history / CLAUDE.md):

  1. THRESHOLD. `fetched` is post-Yishun-keyword-filter for EVERY source, not
     just discovery-tier — every `scrapers.scrape_*` module calls
     `content_matches_keywords`/`content_matches_lang` before returning
     anything (see ingestion/sources/legacy.py). A prior fix gave discovery
     sources a 30-pass leash but left primary sources at 5, which fired as
     "anomalous" on ordinary Yishun silence. There is now one threshold
     (`ZERO_STREAK_ANOMALY`, imported from `ingestion.health.ZERO_STREAK_WARNING`
     so the two can't drift apart again), for every source.

  2. RE-ANNOUNCEMENT. The email dedup key used to embed the sorted broken-
     source list, so any churn in membership (a source crossing in or out of
     "anomalous") changed the key and defeated the once-a-day throttle — the
     same standing problem mailed twice in a day with "slightly different"
     source lists. The fix compares a SIGNATURE (what's broken) against the
     signature from the last actual email, and only sends on a real change.

Run: .venv/Scripts/python.exe test_supervisor_alerting.py
"""
import importlib
import logging
from datetime import datetime, timedelta, timezone
from unittest import mock

logging.disable(logging.CRITICAL)

sup = importlib.import_module("ops.supervisor")

passed = failed = 0
def check(name, cond):
    global passed, failed
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    passed += 1 if cond else 0
    failed += 0 if cond else 1


# ── Fake Supabase (same shape as test_ops_agents.py's — filters ignored) ─────

class _Query:
    def __init__(self, table, rows, sink):
        self._table, self._rows, self._sink = table, rows, sink

    def insert(self, payload, *a, **kw):
        rows = payload if isinstance(payload, list) else [payload]
        self._sink.setdefault(self._table, []).extend(rows)
        return _Query(self._table, [], self._sink)

    def select(self, *a, **kw):  return self
    def update(self, *a, **kw):  return self
    def eq(self, *a, **kw):      return self
    def gte(self, *a, **kw):     return self
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


SENT = {"status": "sent", "id": "n-1", "error": None}
NOW = datetime(2026, 8, 29, 14, 58, tzinfo=timezone.utc)


def state(name, status="blocked", failures=4, hours_ago=1):
    stamp = (NOW - timedelta(hours=hours_ago)).isoformat()
    return {"source_name": name, "last_status": status, "watermark": "2026-08-28",
            "consecutive_failures": failures, "last_run_at": stamp,
            "last_reason": "captcha challenge", "updated_at": stamp}


def codes(findings):
    return [f["code"] for f in findings]


# ── (1a) a real zero-match streak below the (now unified) threshold is quiet ─

print("supervisor — unified zero-streak threshold:")

below = sup.classify_findings(
    pipeline_state=[{"source_name": "cna", "last_status": "ok",
                     "last_run_at": NOW.isoformat(), "consecutive_failures": 0}],
    streaks=[{"source_name": "cna", "consecutive_zeros": sup.ZERO_STREAK_ANOMALY - 1}],
    now=NOW)
check("a PRIMARY source's real zero-match streak, one short of the threshold, "
      "stays quiet", below == [])

at = sup.classify_findings(
    pipeline_state=[{"source_name": "cna", "last_status": "ok",
                     "last_run_at": NOW.isoformat(), "consecutive_failures": 0}],
    streaks=[{"source_name": "cna", "consecutive_zeros": sup.ZERO_STREAK_ANOMALY}],
    now=NOW)
check("...and the same source at the threshold fires (the leash isn't infinite)",
      codes(at) == ["zero_streak"])

check("primary and discovery-tier ids share the SAME threshold now — no more "
      "per-tier split", not hasattr(sup, "_is_discovery")
      and not hasattr(sup, "DISCOVERY_ZERO_STREAK_ANOMALY"))

health = importlib.import_module("ingestion.health")
check("the threshold is imported from ingestion/health.py, not a second copy "
      "of the number", sup.ZERO_STREAK_ANOMALY == health.ZERO_STREAK_WARNING)

# ── (1b) a genuinely errored/dead source still alerts, independent of the
#         zero-streak check (which correctly stays silent on it — no evidence
#         either way, see zero_streaks()'s own skip of non-ok/degraded rows) ──

dead = sup.classify_findings(
    pipeline_state=[state("stomp", "blocked", failures=5)], now=NOW)
check("a source stuck 'blocked' for 5 passes alerts on its OWN detection path "
      "(pipeline_state), unaffected by the zero-streak threshold",
      codes(dead) == ["source_blocked"] and dead[0]["level"] == "anomaly")

no_evidence = sup.zero_streaks([
    {"ran_at": "2026-08-29T02:58:00+00:00",
     "report": {"per_source": [{"name": "stomp", "status": "blocked", "fetched": 0}]}},
])
check("...and a blocked pass contributes NO zero-streak evidence at all "
      "(would otherwise double-count the same outage two ways)",
      no_evidence == [])


# ── (2) state-change dedup: same standing set stays quiet, a new one alerts ──

print("\nsupervisor — alert re-announcement:")

three_down = [state("cna"), state("stomp"), state("zaobao")]

sig_a = sup._alert_signature(sup.classify_findings(pipeline_state=three_down, now=NOW))
sig_b = sup._alert_signature(sup.classify_findings(pipeline_state=three_down, now=NOW))
check("_alert_signature is stable across two identical passes", sig_a == sig_b)

four_down = three_down + [state("mothership")]
sig_c = sup._alert_signature(sup.classify_findings(pipeline_state=four_down, now=NOW))
check("...and changes the moment a new source joins", sig_c != sig_a)

# First pass ever: nothing on record -> must alert.
first = FakeSupabase(pipeline_state=three_down)
with mock.patch.object(sup, "notify", return_value=SENT) as n1:
    stats1 = sup.run(supabase_client=first, now=NOW)
check("first pass with no prior alert on record -> emails", n1.call_count == 1)
sent_sig = next(e["detail"]["alert_signature"]
                for e in first.inserted["agent_events"] if e["event"] == "operator_notified")
check("...and records the signature it alerted on (all 3 registered sources "
      "down also trips all_sources_failing, its own pseudo-member)",
      set(sent_sig) == {"cna", "stomp", "zaobao", "__all_sources_failing__"})

# Second pass, same three sources still down, previous alert now on record:
# must NOT re-mail even though it is still "serious".
prior_event = {"created_at": NOW.isoformat(), "agent": "supervisor", "level": "info",
               "event": "operator_notified", "message": "prior alert",
               "source_name": None, "detail": {"alert_signature": sent_sig}}
unchanged = FakeSupabase(pipeline_state=three_down, agent_events=[prior_event])
with mock.patch.object(sup, "notify", return_value=SENT) as n2:
    stats2 = sup.run(supabase_client=unchanged, now=NOW)
check("unchanged standing-anomaly set on the next pass -> does NOT re-alert",
      n2.call_count == 0)
check("...but 'serious' escalation semantics are still intact (still true)",
      stats2["serious"] is True)

# Third pass: a NEW source breaks on top of the same standing three -> must mail.
worsened = FakeSupabase(pipeline_state=four_down, agent_events=[prior_event])
with mock.patch.object(sup, "notify", return_value=SENT) as n3:
    stats3 = sup.run(supabase_client=worsened, now=NOW)
check("a newly-broken source on top of an unchanged standing set -> DOES alert",
      n3.call_count == 1)

# And the mirror case: one source recovers while a different one newly
# breaks — same COUNT (still 3, still serious) but a different membership, so
# only the signature comparison (not is_serious()'s count) can catch it.
swapped = FakeSupabase(pipeline_state=[state("cna"), state("stomp"), state("mothership")],
                       agent_events=[prior_event])
with mock.patch.object(sup, "notify", return_value=SENT) as n4:
    sup.run(supabase_client=swapped, now=NOW)
check("zaobao recovering while mothership newly breaks (count unchanged) still "
      "counts as a change -> alerts", n4.call_count == 1)

# dedup_key sanity: must still be namespaced, and must not be a fixed
# once-a-day key that would block a genuinely new same-day problem.
kwargs3 = n3.call_args.kwargs
check("dedup_key is namespaced under 'supervisor:'",
      kwargs3["dedup_key"].startswith("supervisor:"))
check("dedup_key reflects the NEW signature (not just the date), so a second, "
      "different problem later the same day would get its own key",
      "mothership" in kwargs3["dedup_key"])


print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
