"""
Orchestrator cluster-write wiring (the 'on' path). Offline.

Run: .venv/Scripts/python.exe test_orchestrator_clustering.py

Proves: per-candidate _emit writes one single-source row (the byte-identical
off-mode unit), and _write_clusters writes ONE merged row per confirmed cluster
with all its sources — while a rejected merge stays two separate rows.
"""
import importlib
from datetime import date, datetime, timedelta, timezone
from unittest import mock

orch = importlib.import_module("ingestion.orchestrator")
from ingestion.contracts import Candidate  # noqa: E402
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


def judge_stab(a, b):
    stab = lambda c_: "stab" in c_.title.lower()
    return stab(a) and stab(b)


with _patched(), mock.patch.object(orch, "_make_merge_judge", return_value=judge_stab):
    client = FakeClient()
    res = orch._write_clusters(
        gathered, client=client, dry_run=False, reputation={}, signal_summary="",
        notes=[], activity=None, deadline=datetime.now(timezone.utc) + timedelta(hours=1),
        circuit_breaker_n=5,
    )

check("2 rows written (X merged, Y separate)", len(client.rows) == 2, f"-> {len(client.rows)}")
merged = [r for r in client.rows if len(r["raw_content"]["source_urls"]) == 2]
check("the stabbing cluster is one 2-source row", len(merged) == 1)
check("merged row carries both outlets", merged and sorted(merged[0]["raw_content"]["source_urls"]) == ["u/cna", "u/msn"])
check("corroboration_count reflects the merge (2)", merged and merged[0]["corroboration_count"] == 2)
check("res counts: 2 queued, 2 new", res["queued"] == 2 and res["new"] == 2)
check("per-source watermarks recorded for written sources",
      set(res["per_source_max"].keys()) == {"CNA", "MSN", "ST"})

# ── judge rejects all -> no merge, 3 single-source rows ─────────────────────
with _patched(), mock.patch.object(orch, "_make_merge_judge", return_value=lambda a, b: False):
    client = FakeClient()
    res = orch._write_clusters(
        gathered, client=client, dry_run=False, reputation={}, signal_summary="",
        notes=[], activity=None, deadline=datetime.now(timezone.utc) + timedelta(hours=1),
        circuit_breaker_n=5,
    )
check("judge rejects everything -> 3 single-source rows", len(client.rows) == 3)
check("no row has >1 source when nothing confirmed",
      all(len(r["raw_content"]["source_urls"]) == 1 for r in client.rows))

# ── dry_run writes nothing ──────────────────────────────────────────────────
with _patched(), mock.patch.object(orch, "_make_merge_judge", return_value=judge_stab):
    client = FakeClient()
    res = orch._write_clusters(
        gathered, client=client, dry_run=True, reputation={}, signal_summary="",
        notes=[], activity=None, deadline=datetime.now(timezone.utc) + timedelta(hours=1),
        circuit_breaker_n=5,
    )
check("dry_run: nothing inserted", len(client.rows) == 0)
check("dry_run: still reports what it would queue", res["queued"] == 2)

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
