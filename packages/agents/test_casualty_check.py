"""
Deterministic deaths/injuries validation (filters/casualty_check). Pure, offline.

Run: .venv/Scripts/python.exe test_casualty_check.py

Snippets are real Singapore-news phrasings. The load-bearing property is
asymmetry: the validator FLAGS disagreement and never corrects a value, and it
must not read "no one was killed" or "fighting for his life" as a death.
"""
import importlib
from unittest import mock

cc = importlib.import_module("filters.casualty_check")
sw = importlib.import_module("filters.stage2_writer")

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name} {detail}")


CONFIRMED = ("The 62-year-old man was pronounced dead at the scene by paramedics. "
             "Police said the death of the pedestrian is being investigated.")
UNCONFIRMED = ("The victim, 38, was taken to Khoo Teck Puat Hospital where he "
               "remains in critical condition, fighting for his life.")
NO_MENTION = ("A car caught fire in the basement carpark of a Yishun condominium "
              "on Tuesday evening. SCDF extinguished the blaze.")
NEGATED = ("SCDF said no one was killed in the blaze and nobody died despite the "
           "thick smoke; there were no fatalities.")
INJ_COUNT = ("Three people were taken to hospital after the collision at Yishun "
             "Avenue 2 on Monday morning.")

print("source-language analysis:\n")

a = cc.analyse(CONFIRMED)
check("confirmed past-tense death is detected", a["confirmed_death"], f"-> {a}")
a = cc.analyse(UNCONFIRMED)
check("'critical condition' / 'fighting for his life' is NOT a death",
      not a["confirmed_death"] and a["unconfirmed_only"], f"-> {a}")
a = cc.analyse(NO_MENTION)
check("no casualty language at all -> nothing claimed",
      not a["confirmed_death"] and not a["unconfirmed_only"], f"-> {a}")
a = cc.analyse(NEGATED)
check("'no one was killed' / 'nobody died' is NOT a death (negation handled)",
      not a["confirmed_death"], f"-> {a['death_phrases']}")
a = cc.analyse(INJ_COUNT)
check("an explicit injured count is extracted", a["injury_count"] == 3, f"-> {a}")
a = cc.analyse("Several people were hurt in the incident.")
check("vague 'several people' yields NO count (per the prompt's own rule)",
      a["injury_count"] is None, f"-> {a}")
# Found on live data: "no one was injured" matches \b(one)...(injured).
a = cc.analyse("The tree fell between two cars but no one was injured.")
check("'no one was injured' is NOT an injured count of 1",
      a["injury_count"] is None, f"-> {a}")
a = cc.analyse("Nobody was hurt and none of the residents were taken to hospital.")
check("'nobody was hurt' / 'none ... taken to hospital' yield no count",
      a["injury_count"] is None, f"-> {a}")
check("empty text never raises", cc.analyse("")["confirmed_death"] is False)

print("\nvalidate() — flags disagreement, never corrects:\n")

v = cc.validate(CONFIRMED, 1, None)
check("model says 1 death + source confirms -> agree, no flag", v["ok"], f"-> {v}")

v = cc.validate(UNCONFIRMED, 1, None)
check("model says 1 death but source is only 'critical condition' -> FLAG",
      not v["ok"] and v["flags"][0]["field"] == "deaths", f"-> {v}")
check("...and the flag cites the unconfirmed language it found",
      any("critical condition" in e for e in v["flags"][0]["source_evidence"]), f"-> {v}")

v = cc.validate(CONFIRMED, 0, None)
check("model says 0 deaths but source confirms one -> FLAG", not v["ok"], f"-> {v}")
v = cc.validate(CONFIRMED, None, None)
check("model says null deaths but source confirms one -> FLAG", not v["ok"], f"-> {v}")

v = cc.validate(NO_MENTION, None, None)
check("source silent + model null -> agreement, no flag", v["ok"], f"-> {v}")
v = cc.validate(NEGATED, 0, None)
check("source explicitly says nobody died + model 0 -> agreement", v["ok"], f"-> {v}")

v = cc.validate(INJ_COUNT, None, 3)
check("explicit injured count matching the model -> no flag", v["ok"], f"-> {v}")
v = cc.validate(INJ_COUNT, None, 1)
check("explicit injured count DISAGREEING with the model -> FLAG",
      not v["ok"] and v["flags"][0]["field"] == "injuries", f"-> {v}")
v = cc.validate(INJ_COUNT, None, None)
check("explicit injured count but model reported none -> FLAG", not v["ok"], f"-> {v}")
v = cc.validate("Several people were hurt.", None, None)
check("vague injuries + model null -> no flag (ambiguity is not disagreement)",
      v["ok"], f"-> {v}")

v = cc.validate(UNCONFIRMED, 1, None)
check("a flag NEVER carries a corrected value — only the model's own",
      "corrected" not in str(v) and v["flags"][0]["model_value"] == 1, f"-> {v}")

v = cc.validate("", "not-a-number", "also-not")
check("junk model values never raise", isinstance(v["ok"], bool), f"-> {v}")

print("\nwiring into write_stage2 (must never raise out):\n")

_CLS = {"classification": "dagger", "severity": 4, "confidence": 0.99,
        "block_number": None, "area_name": "Yishun", "latitude": None,
        "longitude": None, "tags": [], "political": False,
        "deaths": 1, "injuries": None}
_DRAFT = {"title": "Yishun x", "summary": "A man was hurt.", "slug": "s",
          "seo_title": "s", "seo_description": "s"}
_CONTENT = {"title": "t", "content": UNCONFIRMED, "url": "u",
            "source_name": "CNA", "date": "2026-07-14", "source_urls": ["u"]}


def _run(content, cls):
    with mock.patch.object(sw, "_get_client", return_value=mock.MagicMock()), \
         mock.patch.object(sw, "_classify", return_value=cls), \
         mock.patch.object(sw, "_write_draft", return_value=dict(_DRAFT)), \
         mock.patch.object(sw, "_enforce_groundedness",
                           side_effect=lambda c, ct, cl, d: (d, {"flagged": False})):
        return sw.write_stage2(content)


r = _run(_CONTENT, _CLS)
check("a deaths/source mismatch is flagged on the draft",
      r["_casualty_check"]["flagged"] is True, f"-> {r.get('_casualty_check')}")
check("the model's deaths value is NOT overwritten", r["deaths"] == 1, f"-> {r['deaths']}")

r = _run({**_CONTENT, "content": CONFIRMED}, _CLS)
check("agreement leaves the row unflagged", r["_casualty_check"]["flagged"] is False)
check("...and still does not touch the value", r["deaths"] == 1)

with mock.patch("filters.casualty_check.validate", side_effect=RuntimeError("regex bug")):
    r = _run(_CONTENT, _CLS)
check("a checker error flags rather than raising out of write_stage2",
      r["_casualty_check"]["flagged"] is True and "checker_error" in str(r["_casualty_check"]))

# The gate must actually stop it publishing.
ap = importlib.import_module("ops.auto_publish")


def _fake_allowlist(urls, domains=None):
    return {"kept": list(urls or []), "dropped_signal": [], "unapproved": []}


with mock.patch("classifiers.source_allowlist.check_source_urls", _fake_allowlist):
    row = {"status": "pending", "agent_confidence": 0.99,
           "proposed_title": "Yishun x", "proposed_summary": "A man was hurt.",
           "raw_content": {"source_urls": ["https://mothership.sg/x"],
                           "date": "2026-07-14",
                           "_casualty_check": {"flagged": True, "flags": []}}}
    ok, reason = ap.check_eligibility(row, 0.95)
check("a casualty mismatch blocks auto-publish", not ok and reason == "casualty_mismatch",
      f"-> {reason}")

with mock.patch("classifiers.source_allowlist.check_source_urls", _fake_allowlist):
    row["raw_content"]["_casualty_check"] = {"flagged": False, "flags": []}
    ok, reason = ap.check_eligibility(row, 0.95)
check("an agreeing row is unaffected", ok and reason == "eligible", f"-> {reason}")

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
