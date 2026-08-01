"""
Orchestrator cluster-write wiring (the 'on' path). Offline.

Run: .venv/Scripts/python.exe test_orchestrator_clustering.py

Proves: per-candidate _emit writes one single-source row (the byte-identical
off-mode unit), and _write_clusters writes ONE merged row per confirmed cluster
with all its sources — while a rejected merge stays two separate rows.
"""
import importlib
import time
from datetime import date, datetime, timedelta, timezone
from unittest import mock

orch = importlib.import_module("ingestion.orchestrator")
from ingestion.contracts import Candidate  # noqa: E402
from ingestion.watermark import WatermarkTracker  # noqa: E402
from consolidation.check import ConsolidationResult  # noqa: E402

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name} {detail}")


class FakeClient:
    """Captures war_room_queue inserts."""
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


def _cand(title, url, day, source_type="msm", name="src"):
    return Candidate(title=title, content=title, url=url, source_name=name,
                     source_type=source_type, published_at=date(2026, 7, day),
                     discovered_via=name)


def _trackers():
    """A fresh watermark tracker per source. pass_date is well after every
    candidate above, so the same-day grace never confuses these assertions —
    test_watermark_advance.py is where that rule is exercised."""
    return {name: WatermarkTracker(name, None, pass_date=date(2026, 8, 1))
            for name in ("CNA", "MSN", "ST", "src")}


def _draft_echo(stage2_input):
    # Draft echoes source_urls so we can inspect what got merged.
    return {"title": stage2_input.get("title", ""), "summary": "s",
            "classification": "clown", "severity": 1, "confidence": 0.8,
            "slug": "s", "source_urls": stage2_input.get("source_urls", [])}


def _row_echo(item, draft, cons, **kw):
    return {"source_url": item.get("url", ""),
            "proposed_title": item.get("title", ""),
            "raw_content": {"source_urls": draft.get("source_urls", [])},
            "corroboration_count": len(draft.get("source_urls", []) or [])}


PATCHES = dict(
    write_stage2=mock.DEFAULT, consolidation_check=mock.DEFAULT,
    build_queue_row=mock.DEFAULT, check_milestones=mock.DEFAULT,
    is_signal_source=mock.DEFAULT,
)


def _patched():
    return mock.patch.multiple(
        orch,
        write_stage2=mock.Mock(side_effect=_draft_echo),
        consolidation_check=mock.Mock(return_value=ConsolidationResult(action="new", matched_incident_id=None)),
        build_queue_row=mock.Mock(side_effect=_row_echo),
        check_milestones=mock.Mock(return_value={}),
        is_signal_source=mock.Mock(side_effect=lambda st, url="": st == "signal"),
    )


print("orchestrator cluster-write:\n")

# ── _emit: one candidate -> one single-source row ───────────────────────────
with _patched():
    client = FakeClient()
    trk = _trackers()
    c = _cand("Yishun stabbing at Block 873 market", "u/a", 1)
    item = orch._candidate_to_item(c)
    s2 = {**item, "source_urls": ["u/a"], "edmw_signal_count": 0}
    queued, is_update = orch._emit(s2, item, False, 0, c, client=client,
                                   dry_run=False, reputation={}, notes=[])
check("_emit writes one row", len(client.rows) == 1)
check("_emit row is single-source", client.rows[0]["raw_content"]["source_urls"] == ["u/a"])
check("_emit returns queued=True, new", queued is True and is_update is False)

# ── _write_clusters: 2 same-story + 1 other -> 2 rows, X merged ─────────────
# Same event, two outlets: share >=2 distinctive tokens (stabbing, market, cigarette).
xa = _cand("Yishun market stabbing over cigarette dispute injures durian seller", "u/cna", 1, name="CNA")
xb = _cand("Cigarette dispute stabbing at Yishun market leaves durian seller hurt", "u/msn", 2, name="MSN")
y = _cand("Lift breakdown traps elderly grandmother at Yishun tower", "u/st", 1, name="ST")
gathered = [{"candidate": g, "item": orch._candidate_to_item(g),
             "is_dateless": False, "source": g.source_name} for g in (xa, xb, y)]


def grouper_stab(cands):
    """One batched decision: the stabbing reports group, everything else splits."""
    stab = [i for i, c in enumerate(cands) if "stab" in c.title.lower()]
    rest = [[i] for i, c in enumerate(cands) if "stab" not in c.title.lower()]
    return ([stab] if stab else []) + rest


with _patched(), mock.patch.object(orch, "_make_grouper", return_value=grouper_stab):
    client = FakeClient()
    trk = _trackers()
    res = orch._write_clusters(
        gathered, client=client, dry_run=False, reputation={}, signal_summary="",
        notes=[], activity=None, deadline_monotonic=time.monotonic() + 3600,
        circuit_breaker_n=5, trackers=trk,
    )

check("2 rows written (X merged, Y separate)", len(client.rows) == 2, f"-> {len(client.rows)}")
merged = [r for r in client.rows if len(r["raw_content"]["source_urls"]) == 2]
check("the stabbing cluster is one 2-source row", len(merged) == 1)
check("merged row carries both outlets", merged and sorted(merged[0]["raw_content"]["source_urls"]) == ["u/cna", "u/msn"])
check("corroboration_count reflects the merge (2)", merged and merged[0]["corroboration_count"] == 2)
check("res counts: 2 queued, 2 new", res["queued"] == 2 and res["new"] == 2)
check("every written source's watermark advanced to its own candidate's date",
      (trk["CNA"].value(), trk["MSN"].value(), trk["ST"].value())
      == (date(2026, 7, 1), date(2026, 7, 2), date(2026, 7, 1)),
      f"-> {[trk[s].value() for s in ('CNA', 'MSN', 'ST')]}")
check("nothing is left holding a watermark back after a clean write",
      all(trk[s].floor is None for s in ("CNA", "MSN", "ST")))

# ── grouper splits everything -> no merge, 3 single-source rows ─────────────
with _patched(), mock.patch.object(
        orch, "_make_grouper", return_value=lambda cands: [[i] for i in range(len(cands))]):
    client = FakeClient()
    trk = _trackers()
    res = orch._write_clusters(
        gathered, client=client, dry_run=False, reputation={}, signal_summary="",
        notes=[], activity=None, deadline_monotonic=time.monotonic() + 3600,
        circuit_breaker_n=5, trackers=trk,
    )
check("grouper splits everything -> 3 single-source rows", len(client.rows) == 3)
check("no row has >1 source when nothing grouped",
      all(len(r["raw_content"]["source_urls"]) == 1 for r in client.rows))

# ── malformed grouper response -> split-safe, never a merge ─────────────────
def grouper_broken(cands):
    raise ValueError("No JSON object in grouper response")


with _patched(), mock.patch.object(orch, "_make_grouper", return_value=grouper_broken):
    client = FakeClient()
    trk = _trackers()
    res = orch._write_clusters(
        gathered, client=client, dry_run=False, reputation={}, signal_summary="",
        notes=[], activity=None, deadline_monotonic=time.monotonic() + 3600,
        circuit_breaker_n=5, trackers=trk,
    )
check("malformed grouper response -> 3 single-source rows, pass survives", len(client.rows) == 3)
check("a broken grouper never produces a merged row",
      all(len(r["raw_content"]["source_urls"]) == 1 for r in client.rows))

# ── grouper unavailable -> keyword-only fallback still groups ───────────────
# (_make_grouper returns None when Anthropic is not configured.)
with _patched(), mock.patch.object(orch, "_make_grouper", return_value=None):
    client = FakeClient()
    trk = _trackers()
    res = orch._write_clusters(
        gathered, client=client, dry_run=False, reputation={}, signal_summary="",
        notes=[], activity=None, deadline_monotonic=time.monotonic() + 3600,
        circuit_breaker_n=5, trackers=trk,
    )
check("grouper unavailable -> keyword fallback used", res["cstats"] == {"grouper": "unavailable"},
      f"-> {res['cstats']}")
check("keyword fallback still merges the same-story pair", len(client.rows) == 2, f"-> {len(client.rows)}")

# ── dry_run writes nothing ──────────────────────────────────────────────────
with _patched(), mock.patch.object(orch, "_make_grouper", return_value=grouper_stab):
    client = FakeClient()
    trk = _trackers()
    res = orch._write_clusters(
        gathered, client=client, dry_run=True, reputation={}, signal_summary="",
        notes=[], activity=None, deadline_monotonic=time.monotonic() + 3600,
        circuit_breaker_n=5, trackers=trk,
    )
check("dry_run: nothing inserted", len(client.rows) == 0)
check("dry_run: still reports what it would queue", res["queued"] == 2)

print("\n_make_grouper (one Haiku call, strict JSON):\n")

# NOT `import consolidation.check as _cc`: the package __init__ rebinds the name
# `check` to the function, so that form yields the function, not the module.
_cc = importlib.import_module("consolidation.check")


def _client_returning(text):
    """Anthropic client stub that returns `text` and records the request."""
    seen = {}

    class _Msgs:
        def create(self, **kw):
            seen.update(kw)
            return type("R", (), {"content": [type("B", (), {"text": text})()]})()

    return type("C", (), {"messages": _Msgs()})(), seen


stub, seen = _client_returning('{"groups": [[0, 1], [2]]}')
with mock.patch.object(_cc, "_get_anthropic_client", return_value=stub):
    g = orch._make_grouper()
    out = g([xa, xb, y])
check("_make_grouper parses a well-formed partition", out == [[0, 1], [2]], f"-> {out}")
check("_make_grouper makes exactly ONE call with all candidates numbered",
      "[0]" in seen["messages"][0]["content"] and "[2]" in seen["messages"][0]["content"])
check("_make_grouper sends every candidate's title", all(
    c.title in seen["messages"][0]["content"] for c in (xa, xb, y)))
check("_make_grouper uses temperature 0", seen.get("temperature") == 0.0)

stub, _ = _client_returning('{"groups": [["0", "1"], ["2"]]}')
with mock.patch.object(_cc, "_get_anthropic_client", return_value=stub):
    out = orch._make_grouper()([xa, xb, y])
check("_make_grouper coerces numeric-string indices", out == [[0, 1], [2]], f"-> {out}")

stub, _ = _client_returning("I think articles 0 and 1 are the same story.")
with mock.patch.object(_cc, "_get_anthropic_client", return_value=stub):
    g = orch._make_grouper()
    try:
        g([xa, xb, y])
        raised = False
    except Exception:
        raised = True
check("_make_grouper raises on prose instead of JSON (caller splits)", raised)

stub, _ = _client_returning('{"clusters": [[0, 1]]}')
with mock.patch.object(_cc, "_get_anthropic_client", return_value=stub):
    try:
        orch._make_grouper()([xa, xb, y])
        raised = False
    except ValueError:
        raised = True
check("_make_grouper raises when 'groups' key is missing", raised)

with mock.patch.object(_cc, "_get_anthropic_client", side_effect=EnvironmentError("no key")):
    check("_make_grouper returns None when Anthropic is unconfigured", orch._make_grouper() is None)

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
