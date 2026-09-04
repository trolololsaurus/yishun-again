"""
Pattern auto-append gating + reversal semantics. Offline.

Run: .venv/Scripts/python.exe test_pattern_autoappend.py

Proves: only candidates at/above the confidence gate are appended; ids already
in the pattern or in excluded_incident_ids are never scored/appended; an append
writes both arrays + a training_signal; dry_run counts but writes nothing.
Match confidence is stubbed (no network).
"""
import importlib
import os

os.environ["PATTERN_AUTO_APPEND_ENABLED"] = "on"
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")  # _anthropic() only needs it present

pa = importlib.import_module("ops.pattern_autoappend")

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name} {detail}")


class FakeQuery:
    def __init__(self, client, table):
        self.client, self.table_name = client, table
        self.op = "select"
        self.payload = None

    def select(self, *a, **k): self.op = "select"; return self
    def insert(self, row):     self.op, self.payload = "insert", row; return self
    def update(self, row):     self.op, self.payload = "update", row; return self
    def eq(self, *a, **k):     return self
    def gte(self, *a, **k):    return self
    def in_(self, *a, **k):    return self
    def order(self, *a, **k):  return self
    def limit(self, *a, **k):  return self

    def execute(self):
        c = self.client
        if self.op == "insert":
            c.inserts.setdefault(self.table_name, []).append(self.payload)
            return type("R", (), {"data": [{"id": "row"}], "count": 1})()
        if self.op == "update":
            c.updates.setdefault(self.table_name, []).append(self.payload)
            return type("R", (), {"data": [{"id": "row"}], "count": 1})()
        data = c.reads.get(self.table_name, [])
        return type("R", (), {"data": list(data), "count": len(data)})()


class FakeClient:
    def __init__(self, patterns, candidates):
        self.reads = {"patterns": patterns, "incidents": candidates}
        self.inserts: dict = {}
        self.updates: dict = {}

    def table(self, name):
        return FakeQuery(self, name)


def _pattern():
    return {"id": "P", "slug": "cats", "title": "Cats", "thesis": "Cat killings.",
            "incident_ids": ["A"], "auto_added_incident_ids": [], "excluded_incident_ids": ["X"]}


CANDS = [
    {"id": "A", "title": "already in", "summary": ""},
    {"id": "X", "title": "excluded",  "summary": ""},
    {"id": "B", "title": "strong match", "summary": ""},
    {"id": "C", "title": "weak match",   "summary": ""},
]

# Stub the Haiku matcher: B is a strong match, everything else is weak.
def _stub_match(client, pattern, examples, incident):
    return (0.92, "clear cat killing") if incident["id"] == "B" else (0.30, "not really")

pa._match_confidence = _stub_match
pa._notify_if_appended = lambda *a, **k: None   # skip the notify/DB path

# ── Real run ────────────────────────────────────────────────────────────────
sb = FakeClient([_pattern()], CANDS)
stats = pa.run(supabase_client=sb, dry_run=False, trigger="manual")

check("only B appended", stats["appended"] == 1, f"appended={stats['appended']}")
check("excluded X and existing A were not scored",
      stats["candidates_scored"] == 2, f"scored={stats['candidates_scored']}")

upd = sb.updates.get("patterns", [])
check("one patterns update written", len(upd) == 1, f"updates={len(upd)}")
check("incident_ids now [A, B]", bool(upd) and upd[0]["incident_ids"] == ["A", "B"],
      upd[0]["incident_ids"] if upd else "<none>")
check("B recorded in auto_added_incident_ids",
      bool(upd) and upd[0]["auto_added_incident_ids"] == ["B"],
      upd[0]["auto_added_incident_ids"] if upd else "<none>")

sig = sb.inserts.get("training_signals", [])
check("training_signal action=pattern_auto_append for B",
      any(s["action"] == "pattern_auto_append" and s["incident_id"] == "B" for s in sig),
      str(sig))

# ── dry_run ───────────────────────────────────────────────────────────────────
sb2 = FakeClient([_pattern()], CANDS)
stats2 = pa.run(supabase_client=sb2, dry_run=True, trigger="manual")
check("dry_run counts B", stats2["appended"] == 1, f"appended={stats2['appended']}")
check("dry_run writes no patterns update", "patterns" not in sb2.updates, str(sb2.updates))
check("dry_run writes no training_signal", "training_signals" not in sb2.inserts, str(sb2.inserts))

# ── disabled ──────────────────────────────────────────────────────────────────
os.environ["PATTERN_AUTO_APPEND_ENABLED"] = "off"
stats3 = pa.run(supabase_client=FakeClient([_pattern()], CANDS))
check("disabled short-circuits", stats3["enabled"] is False and stats3["appended"] == 0, str(stats3))
os.environ["PATTERN_AUTO_APPEND_ENABLED"] = "on"

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
