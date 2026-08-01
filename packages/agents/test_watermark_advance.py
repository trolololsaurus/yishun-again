"""
Watermark advance on SETTLED candidates (the daily re-spend bug). Offline.

Run: .venv/Scripts/python.exe test_watermark_advance.py

The bug (QA 2026-07-30): only a candidate that got WRITTEN to war_room_queue
advanced its source's recency watermark. A candidate Stage 1 rejected, or that
consolidation judged a duplicate of a still-pending queue row, was never written
anywhere — so `dedup.is_duplicate` could not see it next pass and the watermark
would not move past it either. The same article was re-fetched, re-Stage-1'd
(Gemini), re-drafted (two Haiku calls) and re-judged EVERY daily pass, until some
unrelated candidate from that source happened to drag the watermark forward.

What is asserted here, in both write modes:
  1. A settled-but-unwritten candidate advances the watermark, and the next pass
     therefore spends NOTHING on it (the actual cost claim, measured in calls).
  2. It does not do so at the expense of anything unresolved — a transient error,
     a mid-pass budget halt or an aborted cluster phase still gets its retry.
  3. It does not advance onto the pass's own date, because the source is still
     publishing today and RecencyFilter's `<=` would drop the rest of the day.

(2) and (3) are the load-bearing half: they are what makes advancing safe, and
without them this fix silently loses stories instead of merely wasting money.
"""
import importlib
import time
import os
from datetime import date, datetime, timedelta, timezone
from unittest import mock

orch = importlib.import_module("ingestion.orchestrator")
from consolidation.check import ConsolidationResult  # noqa: E402
from ingestion.contracts import Candidate  # noqa: E402
from ingestion.watermark import WatermarkTracker  # noqa: E402

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name} {detail}")


# ── The pass clock. Candidates are dated in July so the same-day grace only
#    applies where a test deliberately dates one on PASS_DATE. ────────────────
PASS_DATE = date(2026, 8, 1)
NOW = datetime(2026, 8, 1, 6, 58, tzinfo=timezone.utc)   # 14:58 SGT, the real slot
START = date(2026, 7, 20)                                 # persisted watermark


def _cand(title, day, *, month=7, source_type="msm", name="CNA", url=None):
    return Candidate(
        title=title, content=title, url=url or f"https://x/{title.replace(' ', '-')}",
        source_name=name, source_type=source_type,
        published_at=date(2026, month, day) if day else None, discovered_via=name,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Part 1 — WatermarkTracker, the arithmetic on its own
# ═══════════════════════════════════════════════════════════════════════════

print("WatermarkTracker:\n")


def _t(original=START, pass_date=PASS_DATE):
    return WatermarkTracker("CNA", original, pass_date=pass_date)


t = _t()
t.decided(_cand("a", 25))
check("a decided candidate advances the watermark", t.value() == date(2026, 7, 25))

t = _t()
t.unresolved(_cand("a", 25))
check("an unresolved candidate advances nothing", t.value() == START)

t = _t()
t.decided(_cand("newer", 30))
t.unresolved(_cand("older", 29))
check("a decided date at/above the retry floor is held back", t.value() == START,
      f"-> {t.value()}")

t = _t()
t.decided(_cand("oldest", 25))
t.unresolved(_cand("middle", 29))
t.decided(_cand("newest", 30))
check("only decided dates strictly BELOW the floor advance", t.value() == date(2026, 7, 25),
      f"-> {t.value()}")
check("the floor is the earliest unresolved date", t.floor == date(2026, 7, 29))

t = _t(original=date(2026, 7, 28))
t.decided(_cand("older-than-start", 25))
check("the watermark never regresses below where the pass started",
      t.value() == date(2026, 7, 28))

# Same-day grace.
t = _t()
t.decided(_cand("today", 1, month=8))
check("a decided candidate dated TODAY does not advance the watermark",
      t.value() == START, f"-> {t.value()}")
check("...it is held as unresolved instead, so it comes back next pass",
      t.floor == PASS_DATE)
t = _t()
t.decided(_cand("tomorrow", 2, month=8))
check("a future-dated candidate is held too", t.value() == START)
t = _t()
t.decided(_cand("yesterday", 31))
check("yesterday's candidate advances normally — the grace is one day, not a ban",
      t.value() == date(2026, 7, 31))

# Dateless.
t = _t()
t.decided(_cand("dateless", None))
check("a dateless decided candidate moves nothing", t.value() == START and t.floor is None)
t = _t()
t.unresolved(_cand("dateless", None))
check("a dateless unresolved candidate sets no floor either", t.floor is None)

# Transitions — the 'on' path holds a gathered candidate, then settles it.
c = _cand("gathered", 25)
t = _t()
t.unresolved(c)
t.decided(c)
check("decided() upgrades a previously-held candidate (the cluster-write path)",
      t.value() == date(2026, 7, 25) and t.floor is None)
t = _t()
t.decided(c)
t.unresolved(c)
check("unresolved() downgrades a previously-decided candidate", t.value() == START)

# Cold start (§5.3): no persisted watermark yet.
t = _t(original=None)
t.decided(_cand("a", 25))
check("cold start: a decided candidate sets the first watermark",
      t.value() == date(2026, 7, 25))
t = _t(original=None)
t.decided(_cand("today", 1, month=8))
check("cold start with only today's news stays None, not today",
      t.value() is None)
t = _t(original=None)
t.unresolved(_cand("a", 25))
check("cold start with everything held stays None", t.value() is None)

t = _t()
t.decided(_cand("a", 25))
check("no hold note when nothing was held back", t.hold_note() is None)
t.unresolved(_cand("b", 24))
note = t.hold_note()
check("a hold note names the date that still needs a pass",
      note is not None and "2026-07-24" in note, f"-> {note}")


# ═══════════════════════════════════════════════════════════════════════════
# Part 2 — end to end through run_ingestion_pass
# ═══════════════════════════════════════════════════════════════════════════

class FakeSource:
    def __init__(self, name, candidates):
        self.name = name
        self.enabled = True
        self._candidates = candidates
        self.fetches = 0

    def fetch(self, since=None):
        self.fetches += 1
        return list(self._candidates)


class FakeStateStore:
    """Stands in for ingestion.state_store, and remembers the watermark per source
    across passes so a second pass really reads what the first one wrote."""

    def __init__(self, initial=None):
        self.marks = dict(initial or {})
        self.writes = []

    def get(self, source_name, client=None):
        return self.marks.get(source_name)

    def update(self, source_name, watermark, status, reason=None, client=None):
        self.writes.append((source_name, watermark, status))
        self.marks[source_name] = watermark

    def record_run(self, report, client=None):
        pass


class FakeBudget:
    """halt_after=N: should_halt() flips True once N Stage 1 calls are recorded."""

    def __init__(self, halt_after=None):
        self.records = 0
        self.halt_after = halt_after

    def record(self, *_a):
        self.records += 1

    def should_halt(self):
        return self.halt_after is not None and self.records >= self.halt_after

    def mark_rpd_exhausted(self):
        pass


class FakeClient:
    def __init__(self):
        self.rows = []

    def table(self, name):
        client = self

        class _Q:
            def insert(self, row):
                self._row = row
                return self

            def execute(self):
                if name == "war_room_queue":
                    client.rows.append(self._row)
                return type("R", (), {"data": [{"id": f"id-{len(client.rows)}"}]})()
        return _Q()


def _run(sources, store, *, mode="off", reject=(), skip=(), fail=(),
         budget=None, now=NOW, grouper=None, max_duration_seconds=1200):
    """
    One ingestion pass with every model call and DB write stubbed.

    reject: titles Stage 1 rejects.   skip: titles consolidation calls a duplicate.
    fail:   titles whose Stage 2 raises (a transient error, not a verdict).
    Returns (report, calls) where calls counts the spend that matters.
    """
    client = FakeClient()
    calls = {"stage1": 0, "stage2": 0}

    def _stage1(item):
        calls["stage1"] += 1
        return {"passes": item["title"] not in reject, "usage": {},
                "rate_limiter_sleep_seconds": 0.0}

    def _stage2(stage2_input):
        calls["stage2"] += 1
        title = stage2_input.get("title", "")
        if title in fail:
            raise RuntimeError(f"transient write failure for {title}")
        return {"title": title, "summary": "s", "classification": "clown",
                "severity": 1, "confidence": 0.8, "slug": "s",
                "source_urls": stage2_input.get("source_urls", [])}

    def _cons(draft, supabase_client=None):
        action = "skip" if draft.get("title") in skip else "new"
        return ConsolidationResult(action=action, matched_incident_id=None)

    def _row(item, draft, cons, **kw):
        return {"source_url": item.get("url", ""), "proposed_title": item.get("title", ""),
                "raw_content": {"source_urls": draft.get("source_urls", [])}}

    patches = [
        mock.patch.dict(os.environ, {"CLUSTER_BEFORE_WRITE": mode}),
        mock.patch.object(orch, "get_supabase_client", return_value=client),
        mock.patch.object(orch, "load_daily_budget", return_value=budget or FakeBudget()),
        mock.patch.object(orch, "save_daily_budget", mock.Mock()),
        mock.patch.object(orch, "state_store", store),
        mock.patch.object(orch, "filter_content", side_effect=_stage1),
        mock.patch.object(orch, "write_stage2", side_effect=_stage2),
        mock.patch.object(orch, "consolidation_check", side_effect=_cons),
        mock.patch.object(orch, "build_queue_row", side_effect=_row),
        mock.patch.object(orch, "check_milestones", return_value={}),
        mock.patch.object(orch, "is_signal_source", side_effect=lambda st, url="": st == "signal"),
        # Pinned even in 'off' mode: a real key in the environment would otherwise
        # make _make_grouper reach the network and this file stops being offline.
        mock.patch.object(orch, "_make_grouper", return_value=grouper),
        mock.patch.object(orch.dedup, "is_duplicate", side_effect=lambda c, cl, seen=None: False),
        mock.patch.object(orch.learning, "load_source_reputation", return_value={}),
        mock.patch.object(orch.learning, "load_recent_signal_patterns", return_value=""),
        mock.patch.object(orch.learning, "apply_source_reputation", return_value=(0.0, None)),
    ]
    for p in patches:
        p.start()
    try:
        report = orch.run_ingestion_pass(
            sources, now, max_duration_seconds=max_duration_seconds)
    finally:
        for p in reversed(patches):
            p.stop()
    return report, calls


for mode, grouper in (("off", None), ("on", lambda cands: [[i] for i in range(len(cands))])):
    print(f"\nrun_ingestion_pass, CLUSTER_BEFORE_WRITE={mode}:\n")
    kw = {"mode": mode, "grouper": grouper}

    # ── 0. The pass deadline is a DURATION, not a wall-clock timestamp ───────
    #
    # It used to be `now + max_duration_seconds` compared against
    # `datetime.now()` — two different clocks. Any caller whose `now` is not the
    # real current time got a deadline already in the past, and the pass aborted
    # before fetching anything: empty `per_source`, zero Stage 1 calls, no
    # watermark moved. Indistinguishable from "no news today".
    #
    # This whole file pins `now` to the 14:58 SGT slot, so every assertion below
    # silently depended on the real clock being within max_duration of it — the
    # suite was only valid for ~20 real minutes a day. Guard it directly.
    store = FakeStateStore({"CNA": START})
    src = FakeSource("CNA", [_cand("Yishun void deck fire", 27)])
    ancient = datetime(2020, 1, 1, 6, 58, tzinfo=timezone.utc)
    report, calls = _run([src], store, now=ancient, **kw)
    check("a `now` years in the past does NOT abort the pass on the deadline",
          len(report.per_source) == 1, f"-> per_source={report.per_source}")
    check("...and the source was really fetched", calls["stage1"] >= 1, f"-> {calls}")

    # ── 1. The reported bug: a consolidation duplicate-skip advances ─────────
    store = FakeStateStore({"CNA": START})
    src = FakeSource("CNA", [_cand("already queued elsewhere", 28)])
    report, calls = _run([src], store, skip={"already queued elsewhere"}, **kw)
    check("consolidation skip advances the watermark past the skipped candidate",
          store.marks["CNA"] == date(2026, 7, 28), f"-> {store.marks['CNA']}")
    check("...and nothing was written, so only the watermark could have caught it",
          report.total_queued == 0)
    first_spend = (calls["stage1"], calls["stage2"])

    # The cost claim: run the same source again against the watermark just written.
    report2, calls2 = _run([src], store, skip={"already queued elsewhere"}, **kw)
    check("the next pass spends NOTHING on it (no Gemini, no Haiku)",
          calls2 == {"stage1": 0, "stage2": 0}, f"-> {calls2} (first pass {first_spend})")
    check("...because RecencyFilter now drops it before dedup",
          report2.per_source[0].fetched == 1 and report2.per_source[0].fresh == 0)

    # ── 2. A Stage 1 rejection is a verdict too ─────────────────────────────
    store = FakeStateStore({"CNA": START})
    src = FakeSource("CNA", [_cand("Toa Payoh coffeeshop brawl", 26)])
    _run([src], store, reject={"Toa Payoh coffeeshop brawl"}, **kw)
    check("Stage 1 rejection advances the watermark",
          store.marks["CNA"] == date(2026, 7, 26), f"-> {store.marks['CNA']}")
    _, calls2 = _run([src], store, reject={"Toa Payoh coffeeshop brawl"}, **kw)
    check("a rejected article is not re-sent to Gemini next pass", calls2["stage1"] == 0)

    # ── 3. Same-day grace: never advance onto the pass's own date ───────────
    store = FakeStateStore({"CNA": START})
    src = FakeSource("CNA", [_cand("filed this morning", 1, month=8)])
    _run([src], store, skip={"filed this morning"}, **kw)
    check("a candidate dated today does NOT advance the watermark onto today",
          store.marks["CNA"] == START, f"-> {store.marks['CNA']}")
    check("...so this afternoon's stories from that source are still fetchable",
          store.marks["CNA"] < PASS_DATE)

    # ── 4. A transient failure is not orphaned by its settled siblings ──────
    store = FakeStateStore({"CNA": START})
    src = FakeSource("CNA", [
        _cand("oldest, judged a duplicate", 25),
        _cand("middle, write blew up", 29),
        _cand("newest, judged a duplicate", 30),
    ])
    _run([src], store, skip={"oldest, judged a duplicate", "newest, judged a duplicate"},
         fail={"middle, write blew up"}, **kw)
    check("the watermark stops below the candidate that errored",
          store.marks["CNA"] == date(2026, 7, 25), f"-> {store.marks['CNA']}")
    _, calls2 = _run([src], store, skip=set(), fail=set(), **kw)
    check("the errored candidate really is retried next pass", calls2["stage2"] == 2,
          f"-> {calls2}")

# ── 5. A mid-pass Stage 1 budget halt does not lose the remainder ───────────
# Newest-first, as RSS lists it: the pass settles the newest candidate and then
# halts. Advancing to that one would drop the two older ones it never examined.
print("\nmid-pass halts and aborts:\n")
store = FakeStateStore({"CNA": START})
src = FakeSource("CNA", [_cand("newest", 30), _cand("middle", 29), _cand("oldest", 28)])
report, calls = _run([src], store, skip={"newest"}, budget=FakeBudget(halt_after=1))
check("budget halt: the watermark holds below the unexamined remainder",
      store.marks["CNA"] == START, f"-> {store.marks['CNA']}")
check("budget halt: only one candidate reached Stage 1", calls["stage1"] == 1)
_, calls2 = _run([src], store)
check("budget halt: all three are offered again next pass", calls2["stage1"] == 3,
      f"-> {calls2}")

# ── 6. An aborted cluster-write phase must not advance what it never wrote ──
# Driven through _write_clusters directly rather than through a pass, because
# tripping the deadline mid-pass would need a real elapsed wait. The deadline is
# a MONOTONIC value (a duration from an arbitrary origin), not a wall-clock
# timestamp — see the comment on `deadline_monotonic` in orchestrator.py for why
# it stopped being derived from `now`.
_cna_c = _cand("Yishun cat rescued from tree", 28)
_st_c = _cand("Yishun lift breakdown", 26, name="ST")
_gathered = [{"candidate": c, "item": orch._candidate_to_item(c),
              "is_dateless": False, "source": c.source_name} for c in (_cna_c, _st_c)]


def _gathered_trackers():
    """Trackers in the state the gather step leaves them: every candidate held."""
    trk = {name: WatermarkTracker(name, START, pass_date=PASS_DATE) for name in ("CNA", "ST")}
    for g in _gathered:
        trk[g["source"]].unresolved(g["candidate"])
    return trk


def _write_clusters(trackers, deadline_monotonic):
    patches = [
        mock.patch.object(orch, "write_stage2", side_effect=lambda s2: {
            "title": s2.get("title", ""), "summary": "s", "classification": "clown",
            "severity": 1, "confidence": 0.8, "slug": "s",
            "source_urls": s2.get("source_urls", [])}),
        mock.patch.object(orch, "consolidation_check",
                          return_value=ConsolidationResult(action="new", matched_incident_id=None)),
        mock.patch.object(orch, "build_queue_row", side_effect=lambda item, draft, cons, **kw: {
            "source_url": item.get("url", ""), "raw_content": {}}),
        mock.patch.object(orch, "check_milestones", return_value={}),
        mock.patch.object(orch.learning, "apply_source_reputation", return_value=(0.0, None)),
    ]
    for p in patches:
        p.start()
    try:
        return orch._write_clusters(
            _gathered, client=FakeClient(), dry_run=False, reputation={},
            signal_summary="", notes=[], activity=None, deadline_monotonic=deadline_monotonic,
            circuit_breaker_n=5, trackers=trackers)
    finally:
        for p in reversed(patches):
            p.stop()


trk = _gathered_trackers()
res = _write_clusters(trk, time.monotonic() - 1)   # already expired
check("cluster phase hit the deadline before writing anything", res["aborted"] and res["queued"] == 0)
check("aborted cluster phase advances no watermark",
      (trk["CNA"].value(), trk["ST"].value()) == (START, START),
      f"-> {[trk[s].value() for s in ('CNA', 'ST')]}")
check("...and both sources still hold a floor, so both candidates retry",
      trk["CNA"].floor == date(2026, 7, 28) and trk["ST"].floor == date(2026, 7, 26))

trk = _gathered_trackers()
res = _write_clusters(trk, time.monotonic() + 3600)   # plenty of time
check("a completed cluster phase settles every held candidate", res["queued"] == 2)
check("...and each source advances to its own member's date",
      (trk["CNA"].value(), trk["ST"].value()) == (date(2026, 7, 28), date(2026, 7, 26)),
      f"-> {[trk[s].value() for s in ('CNA', 'ST')]}")

# A pass where every candidate fails Stage 1 has no cluster to write — and used
# to advance nothing, which was this bug in its purest form.
store = FakeStateStore({"CNA": START})
src = FakeSource("CNA", [_cand("Bukit Merah fire", 27)])
report, calls = _run([src], store, mode="on", reject={"Bukit Merah fire"},
                     grouper=lambda cands: [[i] for i in range(len(cands))])
check("'on' mode with nothing to cluster still advances the watermark",
      store.marks["CNA"] == date(2026, 7, 27), f"-> {store.marks['CNA']}")

# A signal member merged into a cluster must advance too: guardrail #2 keeps its
# URL out of source_urls, so dedup can NEVER see it and the watermark is the only
# thing standing between it and a re-draft every pass — after which, with its MSM
# siblings deduped away, it would come back alone as an unverified signal-only row.
store = FakeStateStore({"CNA": START, "reddit": START})
cna = FakeSource("CNA", [_cand("Yishun kopitiam stabbing over durian", 28)])
red = FakeSource("reddit", [_cand("anyone see the Yishun kopitiam stabbing durian thing", 28,
                                  source_type="signal", name="reddit")])
report, calls = _run([cna, red], store, mode="on",
                     grouper=lambda cands: [list(range(len(cands)))])
check("a clustered signal member advances its own source's watermark",
      store.marks["reddit"] == date(2026, 7, 28), f"-> {store.marks['reddit']}")
check("...alongside the MSM member it corroborated",
      store.marks["CNA"] == date(2026, 7, 28), f"-> {store.marks['CNA']}")

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
