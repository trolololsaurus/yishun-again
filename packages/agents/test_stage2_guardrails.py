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
# the operator alert + agent_events warning never fired. The guardrail was
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

# ── write_stage2: political content must never reach the writer model ───────
#
# REGRESSION (found live 2026-08-03, running the fixed pipeline's dry run
# against production — the same MP-resignation article that first exposed the
# guardrail-#4 crash in _classify). write_stage2() called _write_draft()
# UNCONDITIONALLY after classification, even when political=True. Asked to
# write dry tabloid copy about an "incident" that by definition isn't one,
# Haiku correctly refused with plain prose ("I cannot process this
# submission... Out of scope...") instead of JSON, and _parse_json's failure
# ("No JSON object in model response") propagated as an UNCAUGHT exception —
# there was no try/except around the _write_draft call in write_stage2.
#
# In the live pipeline that surfaced as ingestion/orchestrator.py's
# "cluster write error", which cannot distinguish a genuine transient failure
# from a deterministic refusal: it marks the whole cluster unresolved and
# retries next pass. A political candidate never stops refusing, so it — and
# any sibling merged into the same cluster, innocent or not — got stuck
# behind the watermark's retry floor, re-spending a Haiku write call on the
# identical refusal every single day.
print("\nwrite_stage2 (political content skips the writer call):")

POLITICAL_CLASSIFY_PAYLOAD = {
    "classification": None, "severity": 3, "confidence": 0.9,
    "block_number": None, "area_name": "Yishun", "latitude": None,
    "longitude": None, "tags": [], "deaths": None, "injuries": None,
    "political": True,
}
CONTENT_POLITICAL = {
    "title": "Singaporeans share accounts of an MP's kindness after resignation",
    "content": "SINGAPORE: After the MP resigned from political office...",
    "url": "https://theindependent.sg/mp-resignation-tributes/",
    "source_name": "The Independent Singapore",
    "source_urls": ["https://theindependent.sg/mp-resignation-tributes/"],
    "edmw_signal_count": 0,
    "date": "2026-07-22",
}

fake = _fake_client(POLITICAL_CLASSIFY_PAYLOAD)
with mock.patch.object(sw, "_get_client", return_value=fake), \
     mock.patch.object(sw, "_write_draft") as wd:
    result = sw.write_stage2(CONTENT_POLITICAL)

check("write_stage2 does not raise on political content", result is not None)
check("the writer model is never called for political content", wd.call_count == 0)
check("only the classify call hits the model (one create() call, not two)",
      fake.messages.create.call_count == 1)
check("confidence is 0.0", result["confidence"] == 0.0)
check("political flag propagates to the queue row", result["political"] is True)
check("reject marker is prepended", result["summary"].startswith("[POLITICAL CONTENT DETECTED"))
check("_political_flagged metadata is attached", "_political_flagged" in result)
for key in ("title", "summary", "slug", "seo_title", "seo_description"):
    check(f"stub draft still carries required field {key!r}", key in result)
check("groundedness is marked skipped, not silently passed",
      result["_groundedness"].get("skipped") == "political" and
      result["_groundedness"]["flagged"] is False)

# A NON-political row must still call the writer model as normal — this fix
# must not skip drafting for ordinary content.
NORMAL_CLASSIFY_PAYLOAD = {**base, "political": False}
NORMAL_WRITE_PAYLOAD = {
    "title": "Fire breaks out in Yishun flat", "summary": "A fire broke out.",
    "slug": "fire-yishun-flat", "seo_title": "Fire in Yishun",
    "seo_description": "A fire broke out in a Yishun flat.",
}
call_count = {"n": 0}
fake2 = mock.MagicMock()


def _dispatch(*a, **k):
    call_count["n"] += 1
    payload = NORMAL_CLASSIFY_PAYLOAD if call_count["n"] == 1 else NORMAL_WRITE_PAYLOAD
    msg = mock.MagicMock()
    msg.content = [mock.MagicMock(text=json.dumps(payload))]
    return msg


fake2.messages.create.side_effect = _dispatch
with mock.patch.object(sw, "_get_client", return_value=fake2), \
     mock.patch.object(sw, "find_ungrounded", return_value={"numbers": [], "proper_nouns": []}):
    result2 = sw.write_stage2({**CONTENT_POLITICAL, "title": "Fire in Yishun flat"})
check("non-political content still calls the writer model", fake2.messages.create.call_count == 2)
check("non-political result carries the drafted title", result2["title"] == "Fire breaks out in Yishun flat")

# ── event_date: incident_date is the EVENT date, not the report date ────────
# REGRESSION (found 2026-08-04). Nothing ever extracted an event date; the
# candidate's publication date was carried straight through, so incidents were
# filed on the day they were REPORTED:
#   python worksite   event Jul 30 ("on July 30 at about 3.57pm")  filed Aug 3
#   high-beam chase   event Jul 31 ("Last Friday (31 July)")       filed Aug 3
#   pliers assault    event Aug 2  ("on Sunday (Aug 2)")           filed Aug 3
print("\nevent date:")

r = sw._classify(_fake_client({**base, "event_date": "2026-07-30"}),
                 {"title": "x", "content": "y", "date": "2026-08-03"})
check("an event date before publication is kept", r["event_date"] == "2026-07-30")

r = sw._classify(_fake_client({**base, "event_date": None}),
                 {"title": "x", "content": "y", "date": "2026-08-03"})
check("no event date -> None (caller falls back to publication)", r["event_date"] is None)

# An event cannot happen after it was reported. A model resolving "Sunday" the
# wrong way produces exactly this, and it would date the row into the future.
r = sw._classify(_fake_client({**base, "event_date": "2026-08-09"}),
                 {"title": "x", "content": "y", "date": "2026-08-03"})
check("an event date AFTER publication is rejected", r["event_date"] is None)

r = sw._classify(_fake_client({**base, "event_date": "2026-08-03"}),
                 {"title": "x", "content": "y", "date": "2026-08-03"})
check("same-day event is valid", r["event_date"] == "2026-08-03")

# >5y before publication is almost certainly a misparse of some other date in
# the copy (a court story citing an old conviction).
r = sw._classify(_fake_client({**base, "event_date": "2010-01-01"}),
                 {"title": "x", "content": "y", "date": "2026-08-03"})
check("an implausibly old event date is rejected", r["event_date"] is None)

for junk in ["not a date", "2026-13-45", "", 20260803, {"d": 1}]:
    r = sw._classify(_fake_client({**base, "event_date": junk}),
                     {"title": "x", "content": "y", "date": "2026-08-03"})
    check(f"malformed event_date {junk!r} -> None (no crash)", r["event_date"] is None)

# No publication date to compare against: accept a well-formed date rather than
# discard the only signal available.
r = sw._classify(_fake_client({**base, "event_date": "2026-07-30"}),
                 {"title": "x", "content": "y"})
check("event date survives when there is no publication date",
      r["event_date"] == "2026-07-30")

# ── Model selection ─────────────────────────────────────────────────────────
print("\nwrite model:")
check("MODEL_WRITE is Haiku", sw.MODEL_WRITE == "claude-haiku-4-5-20251001")
check("MODEL_WRITE is env-overridable (rollback is config, not redeploy)",
      "STAGE2_WRITE_MODEL" in open(sw.__file__, encoding="utf-8").read())

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
