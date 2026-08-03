"""
Few-shot learning context (ingestion/learning.load_recent_signal_patterns).
Offline — a stub client stands in for Supabase.

Run: .venv/Scripts/python.exe test_learning_examples.py

The load-bearing property is that the block carries CONCRETE examples, not
aggregate counts. "Operators re-classified 3 item(s) from 'clown' to 'dagger'"
is unusable by a frozen model: it says a correction happened but not which kind
of story was corrected, so there is nothing to pattern-match against.
"""
import importlib

learning = importlib.import_module("ingestion.learning")

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name} {detail}")


class _Client:
    """Stub Supabase: training_signals + the two title-lookup tables."""
    def __init__(self, signals, queue=None, incidents=None, boom_titles=False):
        self._t = {"training_signals": signals,
                   "war_room_queue": queue or [],
                   "incidents": incidents or []}
        self._boom = boom_titles

    def table(self, name):
        outer, rows = self, self._t.get(name, [])

        class _Q:
            def select(self, *a, **k):  return self
            def order(self, *a, **k):   return self
            def limit(self, *a, **k):   return self
            def in_(self, *a, **k):     return self
            def execute(self):
                if outer._boom and name in ("war_room_queue", "incidents"):
                    raise RuntimeError("supabase down")
                return type("R", (), {"data": rows})()
        return _Q()


def _sig(i, **kw):
    row = {"id": f"s{i}", "decision": "approve", "reject_reason": None,
           "proposed_classification": None, "edited_classification": None,
           "queue_id": f"q{i}", "incident_id": None, "created_at": "2026-07-01"}
    row.update(kw)
    return row


QUEUE = [{"id": f"q{i}", "proposed_title": f"Yishun story number {i}"} for i in range(30)]

print("few-shot learning context:\n")

# Cold start must behave EXACTLY as before: empty string, not an error.
check("cold start (no rows) -> ''", learning.load_recent_signal_patterns(_Client([])) == "")
check("rows but nothing instructive -> ''",
      learning.load_recent_signal_patterns(_Client([_sig(0)], QUEUE)) == "")

# ── Concrete examples, not counts ───────────────────────────────────────────
sigs = [_sig(i, decision="reject", reject_reason="too_thin") for i in range(3)]
out = learning.load_recent_signal_patterns(_Client(sigs, QUEUE))
check("a rejection renders as a titled example", "Yishun story number 0" in out, f"-> {out!r}")
check("...with the operator's reason attached", "too_thin" in out)
check("it is NOT an aggregate count", "item(s)" not in out and "3 item" not in out, f"-> {out!r}")

# A reclassification is the sharper lesson and comes first.
# edited_classification is the column the War Room actually writes on an
# edit_approve — the old corrected_classification was written by nothing.
sigs = [_sig(0, decision="reject", reject_reason="noise"),
        _sig(1, proposed_classification="clown", edited_classification="dagger")]
out = learning.load_recent_signal_patterns(_Client(sigs, QUEUE))
check("a reclassification shows both the proposed and corrected label",
      "clown" in out and "dagger" in out)
check("reclassifications are listed before rejections",
      out.index("clown") < out.index("noise"), f"-> {out!r}")

# ── Round-robin so one reason cannot crowd out the rest ─────────────────────
sigs = ([_sig(i, decision="reject", reject_reason="unverified") for i in range(10)]
        + [_sig(10 + i, decision="reject", reject_reason="too_thin") for i in range(3)]
        + [_sig(20 + i, decision="reject", reject_reason="noise") for i in range(3)])
out = learning.load_recent_signal_patterns(_Client(sigs, QUEUE))
check("all three reject reasons are represented, not just the commonest",
      all(r in out for r in ("unverified", "too_thin", "noise")), f"-> {out!r}")
check("'unverified' (10 of 16 rows) does not fill every slot",
      out.count("unverified") < learning.MAX_EXAMPLES, f"-> {out.count('unverified')}")

# ── Non-teachable reasons never reach the prompt ────────────────────────────
# Stage 2 sees one story and nothing else, so it cannot act on "this duplicates
# another queue row". Before this exclusion 'duplicate' was the single most
# common reason (10 of 30 live rejections) and spent a third of a deliberately
# small budget teaching a lesson the model structurally cannot use.
sigs = ([_sig(i, decision="reject", reject_reason="duplicate") for i in range(10)]
        + [_sig(10 + i, decision="reject", reject_reason="not_yishun") for i in range(2)])
out = learning.load_recent_signal_patterns(_Client(sigs, QUEUE))
check("'duplicate' is excluded from the prompt examples",
      "duplicate" not in out, f"-> {out!r}")
check("...and the teachable reason still gets its slots",
      "not_yishun" in out, f"-> {out!r}")

# The exclusion must not empty the block when duplicates are ALL there is —
# it should degrade to no reject examples rather than to junk.
out = learning.load_recent_signal_patterns(
    _Client([_sig(i, decision="reject", reject_reason="duplicate") for i in range(6)], QUEUE))
check("a duplicate-only history yields no reject examples (not garbage)",
      "duplicate" not in out and "REJECTED" not in out, f"-> {out!r}")

check("'duplicate' is the declared non-teachable reason",
      "duplicate" in learning.NON_TEACHABLE_REJECT_REASONS)
check("'not_yishun' IS teachable (a source/scope lesson Stage 1-2 can act on)",
      "not_yishun" not in learning.NON_TEACHABLE_REJECT_REASONS)

# ── Bounds: this block goes into BOTH prompts ───────────────────────────────
sigs = [_sig(i, decision="reject", reject_reason=f"r{i % 4}") for i in range(30)]
out = learning.load_recent_signal_patterns(_Client(sigs, QUEUE))
check(f"at most MAX_EXAMPLES ({learning.MAX_EXAMPLES}) examples",
      out.count("\n- ") <= learning.MAX_EXAMPLES, f"-> {out.count(chr(10) + '- ')}")
check(f"under the {learning.MAX_EXAMPLES_CHARS}-char ceiling",
      len(out) <= learning.MAX_EXAMPLES_CHARS, f"-> {len(out)}")

long_q = [{"id": "q0", "proposed_title": "Y" * 500}]
out = learning.load_recent_signal_patterns(
    _Client([_sig(0, decision="reject", reject_reason="noise")], long_q))
check("a very long title is truncated, not passed through",
      len(out) < 500 and "Y" * learning.EXAMPLE_TITLE_CHARS in out, f"-> {len(out)}")

# ── Title resolution: two paths, and failure is survivable ──────────────────
sigs = [_sig(0, decision="reject", reject_reason="noise", queue_id=None,
             incident_id="i0")]
out = learning.load_recent_signal_patterns(
    _Client(sigs, [], [{"id": "i0", "title": "Published Yishun incident"}]))
check("title resolves via incident_id when queue_id is absent",
      "Published Yishun incident" in out, f"-> {out!r}")

sigs = [_sig(0, decision="reject", reject_reason="noise", queue_id="missing")]
out = learning.load_recent_signal_patterns(_Client(sigs, []))
check("an unresolvable title skips the example rather than rendering it blank",
      out == "", f"-> {out!r}")

out = learning.load_recent_signal_patterns(
    _Client([_sig(0, decision="reject", reject_reason="noise")], QUEUE, boom_titles=True))
check("a title-lookup outage degrades to '' and never raises", out == "", f"-> {out!r}")

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
