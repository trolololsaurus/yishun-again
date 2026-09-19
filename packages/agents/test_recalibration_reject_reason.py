"""
Recalibration must learn from reject_reason, not just classification/severity/
role edits. Offline.

Run: .venv/Scripts/python.exe test_recalibration_reject_reason.py

Proves: reject_reason is counted by bare reason string (not an edit pair),
only for action='reject' rows; it reaches the correction threshold and gets
written to the calibration log alongside the existing signal types; and the
mistake-generation prompt formats a scalar-keyed counter without an arrow.
"""
import importlib
from collections import Counter
from unittest import mock

recal = importlib.import_module("classifiers.recalibration")

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name} {str(detail).encode('ascii', 'backslashreplace').decode()}")


class FakeQuery:
    def __init__(self, rows):
        self.rows = rows

    def select(self, *a, **k):
        return self

    def execute(self):
        return type("R", (), {"data": self.rows})()


class FakeClient:
    def __init__(self, rows):
        self.rows = rows

    def table(self, name):
        assert name == "training_signals"
        return FakeQuery(self.rows)


# 25x reject_reason='duplicate' (over CORRECTION_THRESHOLD=20), plus a few
# non-reject / other-reason rows that must NOT be folded into that count.
ROWS = (
    [{"action": "reject", "reject_reason": "duplicate"} for _ in range(25)]
    + [{"action": "reject", "reject_reason": "too_thin"} for _ in range(3)]
    + [{"action": "approve", "reject_reason": None,
        "original_classification": "clown", "edited_classification": "dagger"}]
    + [{"action": "reject", "reject_reason": None}]  # reject with no reason -> not counted
)

corrections = recal._fetch_corrections(FakeClient(ROWS))

check("reject_reason counter exists", "reject_reason" in corrections)
check("duplicate counted 25x",
      corrections["reject_reason"]["duplicate"] == 25,
      dict(corrections["reject_reason"]))
check("too_thin counted 3x", corrections["reject_reason"]["too_thin"] == 3)
check("a reject row with no reason is not counted",
      sum(corrections["reject_reason"].values()) == 28)
check("classification counter untouched by reject rows",
      corrections["classification"][("clown", "dagger")] == 1)

# ── _generate_mistakes formats a scalar-keyed counter without an arrow ──────
captured = {}


def _fake_create(**kwargs):
    captured["content"] = kwargs["messages"][0]["content"]
    return type("R", (), {
        "content": [type("B", (), {
            "text": '{"mistakes": ["Check the archive before drafting a new incident."]}'
        })()],
    })()


fake_anthropic = mock.MagicMock()
fake_anthropic.messages.create = _fake_create

mistakes = recal._generate_mistakes(fake_anthropic, "reject_reason", corrections["reject_reason"])
check("mistake text returned", mistakes == ["Check the archive before drafting a new incident."])
# The static instruction text is allowed its own arrow (it explains both
# formats); the per-record DATA lines for a scalar counter must not have one.
ARROW = "→"
TIMES = "×"
data_lines = captured["content"].split("count):\n", 1)[1].split("\n\n")[0]
check("scalar counter formatted as a tally, no arrow in the data lines",
      ARROW not in data_lines and f"25{TIMES} 'duplicate'" in data_lines,
      repr(data_lines))

# ── check() end-to-end: reject_reason reaches the threshold and gets logged ─
with mock.patch.object(recal, "_read_log", return_value=[]), \
     mock.patch.object(recal, "_write_log") as write_log, \
     mock.patch("anthropic.Anthropic", return_value=fake_anthropic), \
     mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
    stats = recal.check(supabase_client=FakeClient(ROWS))

check("recalibrated fires", stats["recalibrated"] is True, stats)
check("reject_reason is among the updated signal types",
      "reject_reason" in stats["signal_types_updated"], stats)
check("calibration log written", write_log.called)
if write_log.called:
    written = write_log.call_args[0][0]
    check("written entry includes reject_reason",
          any(e["signal_type"] == "reject_reason" for e in written), written)

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
