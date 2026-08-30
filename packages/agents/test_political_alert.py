"""
Guardrail #4 fails LOUD (4.3). Offline.

Run: .venv/Scripts/python.exe test_political_alert.py

The guardrail itself is unchanged: political content still has confidence forced
to 0 and still cannot publish. What is added is audibility — a distinct marker,
an operator notification, and a warning-level agent_events row — because under
unattended operation a confidence-0 row is indistinguishable from any other
low-confidence row, so the story vanished without a trace.

Every assertion below therefore checks BOTH halves: the alert fired AND the
guardrail is still exactly as strict.
"""
import importlib
import json
from unittest import mock

sw = importlib.import_module("filters.stage2_writer")
orch = importlib.import_module("ingestion.orchestrator")
ap = importlib.import_module("ops.auto_publish")

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name} {detail}")


def _fake_client(payload):
    c = mock.MagicMock()
    msg = mock.MagicMock()
    msg.content = [mock.MagicMock(text=json.dumps(payload))]
    c.messages.create.return_value = msg
    return c


BASE = {"classification": "dagger", "severity": 3, "confidence": 0.97,
        "block_number": None, "area_name": "Yishun", "latitude": None,
        "longitude": None, "tags": [], "deaths": None, "injuries": None}

print("guardrail #4 is unchanged:\n")

r = sw._classify(_fake_client({**BASE, "political": True, "confidence": 0.97}),
                 {"title": "x", "content": "y"})
check("political still forces confidence to exactly 0.0", r["confidence"] == 0.0)
check("...and the political flag is preserved", r["political"] is True)

r = sw._classify(_fake_client({**BASE, "political": False}), {"title": "x", "content": "y"})
check("non-political confidence is untouched", r["confidence"] == 0.97)

print("\ndistinct marker (not merely 'low confidence'):\n")

CONTENT = {"title": "MP visits Yishun", "content": "An MP visited.",
           "url": "https://cna.com/a", "source_name": "CNA", "date": "2026-07-14",
           "source_urls": ["https://cna.com/a"]}
DRAFT = {"title": "Yishun t", "summary": "s", "slug": "s",
         "seo_title": "s", "seo_description": "s"}


def _stage2(political):
    cls = {**BASE, "political": political, "confidence": 0.0 if political else 0.97}
    with mock.patch.object(sw, "_get_client", return_value=mock.MagicMock()), \
         mock.patch.object(sw, "_classify", return_value=cls), \
         mock.patch.object(sw, "_write_draft", return_value=dict(DRAFT)), \
         mock.patch.object(sw, "_enforce_groundedness",
                           side_effect=lambda c, ct, cl, d: (d, {"flagged": False})):
        return sw.write_stage2(CONTENT)


d = _stage2(True)
check("draft carries a distinct _political_flagged marker", bool(d.get("_political_flagged")))
check("...naming the source URL so the operator can go look",
      d["_political_flagged"]["source_url"] == "https://cna.com/a")
check("...and recording that confidence was forced, not merely low",
      d["_political_flagged"]["confidence_forced_to"] == 0.0)
check("the operator-visible reject marker is still prepended",
      d["summary"].startswith("[POLITICAL CONTENT DETECTED"))
check("confidence is still 0.0 on the draft", d["confidence"] == 0.0)

d = _stage2(False)
check("a non-political draft carries no marker", "_political_flagged" not in d)

print("\nalert: notification + warning-level agent_events:\n")


class _Activity:
    def __init__(self):
        self.events = []

    def event(self, level, event, message, source_name=None, **detail):
        self.events.append({"level": level, "event": event, "message": message,
                            "source_name": source_name, **detail})


class _Cand:
    url = "https://cna.com/a"
    source_name = "CNA"


act = _Activity()
with mock.patch("ops.notify.notify", return_value={"status": "sent"}) as n:
    orch._alert_political({"title": "MP visits Yishun", "political": True},
                          _Cand(), client=None, activity=act)

check("an operator notification is sent", n.call_count == 1)
kw = n.call_args.kwargs if n.call_count else {}
check("...through the EXISTING dedup ledger (dedup_key set)",
      kw.get("dedup_key") == "political:https://cna.com/a", f"-> {kw.get('dedup_key')}")
check("...so a re-run over the same story cannot spam",
      "dedup_key" in kw and kw["dedup_key"].startswith("political:"))
check("an agent_events row is written", len(act.events) == 1)
check("...at level 'warning'", act.events[0]["level"] == "warning", f"-> {act.events[0]['level']}")
check("...carrying the incident title", "MP visits Yishun" in act.events[0]["message"])
check("...and the source URL", "https://cna.com/a" in act.events[0]["message"])

# It must survive a broken notifier — alerting is not worth losing a pass over.
act2 = _Activity()
with mock.patch("ops.notify.notify", side_effect=RuntimeError("telegram down")):
    orch._alert_political({"title": "t", "political": True}, _Cand(),
                          client=None, activity=act2)
check("a notifier outage does not raise (pass survives)", len(act2.events) == 1)

orch._alert_political({"title": "t", "political": True}, _Cand(), client=None, activity=None)
check("no activity run supplied -> still no crash", True)

print("\nno bypass, no config override:\n")

src = open(sw.__file__, encoding="utf-8").read() + open(orch.__file__, encoding="utf-8").read()
for knob in ("POLITICAL_ENABLED", "ALLOW_POLITICAL", "SKIP_POLITICAL",
             "POLITICAL_OVERRIDE", "DISABLE_POLITICAL"):
    check(f"no env switch named {knob}", knob not in src)
check("nothing in the alert path writes back to confidence",
      "confidence" not in orch._alert_political.__code__.co_names,
      f"-> {orch._alert_political.__code__.co_names}")

# Defence in depth: the auto-publish gate still refuses it independently.
def _fake_allowlist(urls, domains=None):
    return {"kept": list(urls or []), "dropped_signal": [], "unapproved": []}


with mock.patch("classifiers.source_allowlist.check_source_urls", _fake_allowlist):
    row = {"status": "pending", "agent_confidence": 0.0,
           "proposed_title": "Yishun t",
           "proposed_summary": "[POLITICAL CONTENT DETECTED — REJECT] s",
           "raw_content": {"source_urls": ["https://cna.com/a"], "date": "2026-07-14"}}
    ok, reason = ap.check_eligibility(row, 0.95)
check("auto-publish still refuses a political row", not ok, f"-> {reason}")

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
