"""
Self-contained tests for graduated-autonomy error-rate arithmetic. No pytest.
Run: .venv/Scripts/python.exe test_autonomy_tracker.py

WHY THIS FILE EXISTS
--------------------
`get_autonomy_status()` computes, per autonomy_signal:

    error_rate = corrections / total

where a training_signals row counts toward `total` if operator_changes carries
an `autonomy_signal`, and toward `corrections` if it ALSO carries a
`dismiss_reason_category`.

Until 2026-08-03 both fields were written by exactly one code path — the War
Room's dismiss-alert route. A confirmed link (confirm-link) recorded no signal
at all. So every counted decision was a rejection, `corrections == total`, and
error_rate was pinned at exactly 1.00. Measured live that day:

    entity_extraction        2 decisions, 2 corrections, error_rate 1.00
    confidence_threshold     5 decisions, 5 corrections, error_rate 1.00
    temporal_dedup           2 decisions, 2 corrections, error_rate 1.00

Every entry in GRADUATION_THRESHOLDS requires error_rate <= 0.10, so nothing
could ever graduate. The autonomy system could only count its own mistakes.

The fix records the operator's AGREEMENT too. These tests pin the arithmetic so
a future edit cannot quietly restore a failures-only denominator.
"""
import importlib
from unittest import mock

at = importlib.import_module("classifiers.autonomy_tracker")

passed = failed = 0


def check(name, cond):
    global passed, failed
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    passed += 1 if cond else 0
    failed += 0 if cond else 1


class _Client:
    """Minimal supabase stand-in: .table().select().execute().data"""
    def __init__(self, rows):
        self._rows = rows

    def table(self, _name):
        return self

    def select(self, *_a, **_k):
        return self

    def execute(self):
        return type("R", (), {"data": self._rows})()


def _dismissed(signal="confidence_threshold", category="LOCATION_COINCIDENCE"):
    """A row shaped like the dismiss-alert route writes."""
    return {"operator_changes": {
        "autonomy_signal": signal,
        "dismiss_reason_category": category,
        "diagnostic_signal": "location_dedup",
    }}


def _confirmed(signal="confidence_threshold"):
    """A row shaped like the confirm-link route writes. The ABSENCE of
    dismiss_reason_category is the whole signal — do not add one."""
    return {"operator_changes": {
        "autonomy_signal": signal,
        "link_confirmed": True,
    }}


def _status(rows):
    with mock.patch.object(at, "_get_client", return_value=_Client(rows)):
        return at.get_autonomy_status()


print("autonomy tracker tests:")

# ── The regression itself ───────────────────────────────────────────────────
only_dismissals = [_dismissed() for _ in range(10)]
st = _status(only_dismissals)["confidence_threshold"]
check("dismissals-only yields error_rate 1.0 (the old, broken state)",
      st["error_rate"] == 1.0)
check("...and therefore cannot graduate", st["graduated"] is False)

# A confirmation must count toward the denominator but NOT the numerator.
mixed = [_confirmed() for _ in range(9)] + [_dismissed()]
st = _status(mixed)["confidence_threshold"]
check("confirmations count toward total_decisions", st["total_decisions"] == 10)
check("confirmations do NOT count as corrections", st["operator_corrections"] == 1)
check("error_rate reflects the real ratio", abs(st["error_rate"] - 0.1) < 1e-9)

# Graduation must now be reachable. confidence_threshold: min_samples 30,
# error_rate_max 0.10 — so 30 confirms and 3 dismissals sits exactly at the line.
reachable = [_confirmed() for _ in range(30)] + [_dismissed() for _ in range(3)]
st = _status(reachable)["confidence_threshold"]
check("a signal CAN graduate once agreements are recorded",
      st["graduated"] is True and st["status"] == "graduated")

# One dismissal too many must fail it — the threshold has to still bite.
too_many = [_confirmed() for _ in range(30)] + [_dismissed() for _ in range(6)]
st = _status(too_many)["confidence_threshold"]
check("too high an error rate still blocks graduation", st["graduated"] is False)

# Enough accuracy but not enough evidence must NOT graduate.
thin = [_confirmed() for _ in range(5)]
st = _status(thin)["confidence_threshold"]
check("a perfect but thin record does not graduate", st["graduated"] is False)
check("...and reports how many more samples it needs",
      st["samples_needed"] == at.GRADUATION_THRESHOLDS["confidence_threshold"]["min_samples"] - 5)

# ── Bookkeeping ─────────────────────────────────────────────────────────────
st = _status([])["confidence_threshold"]
check("no data -> insufficient_data, not a divide-by-zero",
      st["status"] == "insufficient_data" and st["error_rate"] == 0.0)

st = _status([{"operator_changes": {}}, {"operator_changes": None}])
check("rows with no autonomy_signal are ignored",
      st["confidence_threshold"]["total_decisions"] == 0)

st = _status([_confirmed(signal="not_a_real_signal")])
check("an unknown signal is ignored, never invented",
      "not_a_real_signal" not in st)

check("every declared threshold is reported",
      set(_status([]).keys()) == set(at.GRADUATION_THRESHOLDS))

print()
print(f"{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
