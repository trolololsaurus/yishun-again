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

# C1: guardrail #4 must survive a malformed classification.
#
# REGRESSION (found live 2026-08-02, an MP-resignation article surfaced by the
# WordPress search source). `result["classification"].lower()` ran BEFORE the
# political check and threw AttributeError on `"classification": null` — which
# is what the model tends to return on a political story, because it is being
# told to reject rather than categorise. The candidate died on an exception, so
# confidence was never forced to 0, the reject marker was never prepended, and
# the operator email + agent_events warning never fired. The guardrail was
# unreachable for a subset of exactly the content it exists to catch.
r = sw._classify(_fake_client({**base, "classification": None, "political": True,
                               "confidence": 0.9}), {"title": "x", "content": "y"})
check("C1 political + null classification does not crash", r["political"] is True)
check("C1 political + null classification still forces confidence=0", r["confidence"] == 0.0)
check("C1 political + null classification gets a valid placeholder category",
      r["classification"] in ("heart", "clown", "dagger"))

r = sw._classify(_fake_client({**base, "classification": "nonsense", "political": True}),
                 {"title": "x", "content": "y"})
check("C1 political + invalid classification still rejected, not raised",
      r["confidence"] == 0.0 and r["classification"] in ("heart", "clown", "dagger"))

# A NON-political row with a bad classification is still a genuine model
# failure and must keep raising — the placeholder is strictly a guardrail path.
try:
    sw._classify(_fake_client({**base, "classification": None, "political": False}),
                 {"title": "x", "content": "y"})
    check("non-political null classification still raises", False)
except (ValueError, AttributeError):
    check("non-political null classification still raises", True)

# M5: non-numeric deaths/injuries don't crash → None
r = sw._classify(_fake_client({**base, "deaths": "several", "injuries": "two"}), {"title": "x", "content": "y"})
check("M5 non-numeric deaths/injuries -> None (no crash)", r["deaths"] is None and r["injuries"] is None)

# M5: numeric still works
r = sw._classify(_fake_client({**base, "deaths": 2, "injuries": 0}), {"title": "x", "content": "y"})
check("M5 numeric deaths/injuries preserved", r["deaths"] == 2 and r["injuries"] == 0)


# ── Summary char budget: arithmetic, not an instruction ─────────────────────
print("\nsummary_char_budget (source-proportional cap):")

thin = {"title": "t", "content": "x" * 900}
check("thin single source -> a few hundred chars, cannot sprawl",
      sw.summary_char_budget(thin) == int(0.75 * 900))
check("...and that is well under the 1600 ceiling", sw.summary_char_budget(thin) < 1600)

five = {"title": "t", "content": "x" * 900,
        "source_articles": [{"source_type": "msm", "content": "y" * 2500} for _ in range(5)]}
check("5-source cluster -> the full 1600", sw.summary_char_budget(five) == 1600)

check("budget never exceeds the hard ceiling",
      sw.summary_char_budget({"content": "x" * 999999}) == sw.SUMMARY_HARD_CEILING)
check("budget never collapses below the floor",
      sw.summary_char_budget({"content": "x" * 50}) == sw.SUMMARY_FLOOR)

sig = {"title": "t", "content": "x" * 900,
       "source_articles": [{"source_type": "signal", "content": "z" * 40000}]}
check("guardrail #2: signal bodies do not inflate the budget",
      sw.summary_char_budget(sig) == sw.summary_char_budget({"content": "x" * 900}) or
      sw.summary_char_budget(sig) == sw.SUMMARY_FLOOR)

# ── Groundedness check ──────────────────────────────────────────────────────
print("\nfind_ungrounded (deterministic groundedness):")

SRC = ("A 45-year-old man was arrested after a fire broke out at Block 512 "
       "Yishun Street 81 on Tuesday. The Singapore Civil Defence Force said "
       "one person was taken to Khoo Teck Puat Hospital.")

g = sw.find_ungrounded("A fire at Block 512 injured one person, said the "
                       "Singapore Civil Defence Force.", SRC, "2026-07-14")
check("a fully grounded summary passes", not g["numbers"] and not g["proper_nouns"])

g = sw.find_ungrounded("A fire at Block 900 injured one person.", SRC, "2026-07-14")
check("an INVENTED block number fails", "900" in g["numbers"])

g = sw.find_ungrounded("A 45-year-old man was hurt at Block 512.", SRC, "2026-07-14")
check("a real age from the source passes", not g["numbers"])

g = sw.find_ungrounded("Officers from the Yishun Rapid Response Unit attended.",
                       SRC, "2026-07-14")
check("an invented agency name fails", g["proper_nouns"])

g = sw.find_ungrounded("The incident happened in July 2026 at Block 512.", SRC, "2026-07-14")
check("the incident month/year come from the date field, not the body -> pass",
      not g["numbers"] and not g["proper_nouns"])

g = sw.find_ungrounded("On Tuesday, The Singapore Civil Defence Force responded.",
                       SRC, "2026-07-14")
check("a sentence-initial capital glued to a real name -> pass (no false positive)",
      not g["proper_nouns"])

check("empty inputs never raise",
      sw.find_ungrounded("", "", "") == {"numbers": [], "proper_nouns": []})

# ── Regenerate once, then flag ──────────────────────────────────────────────
print("\n_enforce_groundedness (regenerate once, then flag, never raise):")

CONTENT = {"title": "Fire at Yishun", "content": SRC, "date": "2026-07-14"}
CLS = {"classification": "dagger", "severity": 3}

clean_draft = {"summary": "A fire at Block 512 injured one person."}
bad_draft = {"summary": "A fire at Block 900 injured one person."}

d, rep = sw._enforce_groundedness(None, CONTENT, CLS, clean_draft)
check("a clean draft is not regenerated", rep["attempts"] == 1 and not rep["flagged"])

with mock.patch.object(sw, "_write_draft", return_value=clean_draft) as wd:
    d, rep = sw._enforce_groundedness(None, CONTENT, CLS, bad_draft)
check("an ungrounded draft is regenerated ONCE", wd.call_count == 1)
check("...and a clean regeneration is accepted, unflagged",
      d is clean_draft and not rep["flagged"] and rep.get("recovered"))

with mock.patch.object(sw, "_write_draft", return_value=bad_draft) as wd:
    d, rep = sw._enforce_groundedness(None, CONTENT, CLS, bad_draft)
check("still ungrounded after the retry -> FLAGGED, not silently published",
      rep["flagged"] and rep["attempts"] == 2 and "900" in rep["numbers"])
check("...and it regenerates only once, never in a loop", wd.call_count == 1)

with mock.patch.object(sw, "_write_draft", side_effect=RuntimeError("model down")):
    d, rep = sw._enforce_groundedness(None, CONTENT, CLS, bad_draft)
check("a failed regeneration flags rather than raising", rep["flagged"] and d is bad_draft)

with mock.patch.object(sw, "find_ungrounded", side_effect=RuntimeError("checker bug")):
    d, rep = sw._enforce_groundedness(None, CONTENT, CLS, clean_draft)
check("a CHECKER error degrades to flag, never to pass",
      rep["flagged"] and rep["checked"] is False)

# ── Model selection ─────────────────────────────────────────────────────────
print("\nwrite model:")
check("MODEL_WRITE is Haiku", sw.MODEL_WRITE == "claude-haiku-4-5-20251001")
check("MODEL_WRITE is env-overridable (rollback is config, not redeploy)",
      "STAGE2_WRITE_MODEL" in open(sw.__file__, encoding="utf-8").read())

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
