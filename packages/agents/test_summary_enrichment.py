"""
Self-contained test for update-summary enrichment (consolidation/enrich.py).
No pytest. Run: .venv/Scripts/python.exe test_summary_enrichment.py

Offline: mocks the model call so no network. Verifies the merge keeps existing
detail + adds the new development, that an invented specific fails the
groundedness gate (so it is offered for review but never auto-applied), and that
every failure mode fails SAFE (ok=False, empty summary -> caller keeps existing).
"""
import importlib
from unittest import mock

enrich = importlib.import_module("consolidation.enrich")

passed = failed = 0
def check(name, cond):
    global passed, failed
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    passed += 1 if cond else 0
    failed += 0 if cond else 1


def fake_call(text):
    resp = mock.MagicMock()
    resp.content = [mock.MagicMock(text=text)]
    return (resp, False)


EXISTING = ("Foo Jia Hong made 425 silent calls to Yishun NPC and was arrested "
            "in May 2026 after police traced the SIM card.")
NEW_DEV = ("On August 26, District Judge Koo Zhi Xuan sentenced Foo Jia Hong to "
           "23 days jail for obstructing police officers.")

print("summary enrichment tests:")

# A grounded merge: keeps existing facts, adds the sentencing development.
merged = ('{"summary": "Foo Jia Hong made 425 silent calls to Yishun NPC and was '
          'arrested in May 2026 after police traced the SIM card. On August 26, '
          'District Judge Koo Zhi Xuan sentenced him to 23 days jail for '
          'obstructing police officers."}')
with mock.patch.object(enrich, "create_with_headroom", return_value=fake_call(merged)):
    r = enrich.enrich_summary(EXISTING, NEW_DEV, "sentenced", 1600, client=object())
check("grounded merge -> ok", r["ok"] and r["grounded"])
check("preserves existing detail (425)", "425" in r["summary"])
check("weaves in the new development (23 days)", "23 days" in r["summary"])

# Invented specifics -> ungrounded: still returned (operator can fix) but ok=False
# so it can never be auto-applied.
bad = ('{"summary": "Foo Jia Hong made 425 silent calls to Yishun NPC. He was '
       'fined 88888 dollars by Judge Imaginary Person on appeal."}')
with mock.patch.object(enrich, "create_with_headroom", return_value=fake_call(bad)):
    r = enrich.enrich_summary(EXISTING, NEW_DEV, "x", 1600, client=object())
check("invented number -> ungrounded", not r["grounded"])
check("ungrounded -> not ok (never auto-applies)", not r["ok"])
check("ungrounded summary still returned for operator review", r["summary"] != "")

# Failure modes fail SAFE (ok=False, empty summary -> caller keeps existing).
check("empty existing -> not ok", not enrich.enrich_summary("", NEW_DEV, client=object())["ok"])
check("empty new development -> not ok", not enrich.enrich_summary(EXISTING, "", client=object())["ok"])

with mock.patch.object(enrich, "create_with_headroom", side_effect=RuntimeError("boom")):
    r = enrich.enrich_summary(EXISTING, NEW_DEV, client=object())
check("model failure -> fails safe (ok False, empty summary)", not r["ok"] and r["summary"] == "")

with mock.patch.object(enrich, "create_with_headroom", return_value=fake_call('{"summary": ""}')):
    r = enrich.enrich_summary(EXISTING, NEW_DEV, client=object())
check("empty model output -> fails safe", not r["ok"] and r["summary"] == "")

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
