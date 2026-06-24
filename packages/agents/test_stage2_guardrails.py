"""
Self-contained test for the Stage 2 guardrail fixes (QA C1 + M5). No pytest.
Run: .venv/Scripts/python.exe test_stage2_guardrails.py
Mocks the Anthropic client so _classify runs offline.
"""
import json
from unittest import mock
import importlib

sw = importlib.import_module("filters.stage2_writer")

def _fake_client(payload: dict):
    c = mock.MagicMock()
    msg = mock.MagicMock()
    msg.content = [mock.MagicMock(text=json.dumps(payload))]
    c.messages.create.return_value = msg
    return c

passed = failed = 0
def check(name, cond):
    global passed, failed
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    passed += 1 if cond else 0
    failed += 0 if cond else 1

base = {"classification": "dagger", "severity": 3, "confidence": 0.9,
        "block_number": None, "area_name": "Yishun", "latitude": None,
        "longitude": None, "tags": [], "deaths": None, "injuries": None}

print("stage2 guardrail tests:")

# C1: political content → confidence forced to 0 regardless of model confidence
r = sw._classify(_fake_client({**base, "political": True, "confidence": 0.95}), {"title": "x", "content": "y"})
check("C1 political forces confidence=0", r["confidence"] == 0.0 and r["political"] is True)

# C1: non-political unaffected
r = sw._classify(_fake_client({**base, "political": False}), {"title": "x", "content": "y"})
check("C1 non-political keeps confidence", r["confidence"] == 0.9 and r["political"] is False)

# M5: non-numeric deaths/injuries don't crash → None
r = sw._classify(_fake_client({**base, "deaths": "several", "injuries": "two"}), {"title": "x", "content": "y"})
check("M5 non-numeric deaths/injuries -> None (no crash)", r["deaths"] is None and r["injuries"] is None)

# M5: numeric still works
r = sw._classify(_fake_client({**base, "deaths": 2, "injuries": 0}), {"title": "x", "content": "y"})
check("M5 numeric deaths/injuries preserved", r["deaths"] == 2 and r["injuries"] == 0)

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
