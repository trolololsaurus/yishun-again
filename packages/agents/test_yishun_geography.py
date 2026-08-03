"""
Self-contained tests for the Yishun keyword scope (no pytest needed).
Run: .venv/Scripts/python.exe test_yishun_geography.py

WHY THIS FILE EXISTS
--------------------
`YISHUN_KEYWORDS` decides what the entire pipeline considers a Yishun story.
It went unchanged from the initial import (e71d976, 2026-06-06) until
2026-08-02 — while every TechSpec from v1.5 onward carried the line

    # NOTE: "sembawang" removed — separate town, not Yishun

The spec said it was removed. The code never removed it. Nothing tested it, so
nothing caught the disagreement for two months, and Sembawang stories kept
arriving in the queue for the operator to reject by hand ("19-year-old arrested
for plotting knife attacks on Sembawang Air Base soldiers", 2026-07-28).

The rule this file pins: a keyword qualifies only if it names the Yishun
planning area or something inside it. Adjacent towns do not, however close.
"""
from scrapers import YISHUN_KEYWORDS, _YISHUN_RAW, content_matches_keywords, content_matches_lang

passed = failed = 0


def check(name, cond):
    global passed, failed
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    passed += 1 if cond else 0
    failed += 0 if cond else 1


print("yishun geography tests:")

# ── Adjacent towns are NOT Yishun ────────────────────────────────────────────
# Each of these is its own URA planning area. Re-adding any of them is the
# exact regression this file exists to stop.
NOT_YISHUN = [
    ("sembawang",      "Sembawang Air Base incident, 19-year-old charged"),
    ("woodlands",      "Fire breaks out at Woodlands Drive flat"),
    ("admiralty",      "Admiralty MRT station escalator fault"),
    ("canberra",       "New Canberra Plaza mall opens to crowds"),
    ("ang mo kio",     "Ang Mo Kio hawker centre closes for renovation"),
    ("sengkang",       "Sengkang LRT disruption strands commuters"),
]
for town, headline in NOT_YISHUN:
    check(f"{town!r} is not a keyword", town not in YISHUN_KEYWORDS)
    check(f"headline about {town} does not match", not content_matches_keywords(headline))

# The historical regression, stated as its own case so the failure message is
# unambiguous if anyone puts it back.
check("SEMBAWANG IS NOT YISHUN",
      not content_matches_keywords("Lorry overturns on Sembawang flyover"))

# ── Yishun and its subzones DO match ─────────────────────────────────────────
IS_YISHUN = [
    ("yishun",         "Man taken to hospital after fight in Yishun"),
    ("khatib",         "Commuters stranded at Khatib MRT after signal fault"),
    ("chong pang",     "Chong Pang hawkers keep nasi lemak at S$2"),
    ("northpoint",     "Shoplifter caught at Northpoint City"),
    ("khoo teck puat", "Patient absconds from Khoo Teck Puat Hospital"),
]
for kw, headline in IS_YISHUN:
    check(f"{kw!r} is a keyword", kw in YISHUN_KEYWORDS)
    check(f"headline about {kw} matches", content_matches_keywords(headline))

# KHATIB IS YISHUN — the other half of the 2026-08-02 correction. It was absent
# from the list entirely, so a Khatib-only story was invisible to the pipeline.
check("KHATIB IS YISHUN", content_matches_keywords("Fire at Khatib Camp bunk"))

# ── Case insensitivity ───────────────────────────────────────────────────────
check("matching is case-insensitive", content_matches_keywords("YISHUN RING ROAD ACCIDENT"))
check("mixed case matches", content_matches_keywords("Khatib MRT"))

# ── "nee soon" is deliberately excluded from the English list ────────────────
# In news copy it is overwhelmingly the constituency (Nee Soon GRC), not the
# place. Its only hit in a live sample was an article about an MP — content
# guardrail #4 has to reject as political anyway — and every genuine Yishun
# story in that sample already matched on "yishun".
check("'nee soon' is not an English keyword", "nee soon" not in YISHUN_KEYWORDS)
check("Nee Soon GRC headline does not match on its own",
      not content_matches_keywords("Nee Soon GRC MP visits residents"))
check("'Nee Soon' is retained as a Malay place-name",
      any("nee soon" in k.lower() for k in _YISHUN_RAW["ms"]))

# ── Source-language lists carry the same scope ───────────────────────────────
# Test NAMES stay ASCII on purpose: this suite runs on a Windows console whose
# default codepage is cp1252, and printing the CJK/Tamil source text itself
# raises UnicodeEncodeError before the assertion result is ever reported. The
# non-ASCII strings under test are still exercised — just not echoed.
check("zh matches Yishun (yi shun)", content_matches_lang("义顺组屋发生火患", "zh"))
check("zh matches Khatib (ka di)", content_matches_lang("卡迪地铁站故障", "zh"))
check("ms matches Yishun", content_matches_lang("Kemalangan di Yishun Ring Road", "ms"))
check("ms matches Khatib", content_matches_lang("Kebakaran di Khatib", "ms"))
check("ta matches Yishun (transliterated)",
      content_matches_lang("யிஷுன் பகுதியில் விபத்து", "ta"))
check("no language list contains sembawang",
      not any("sembawang" in k.lower() for ks in _YISHUN_RAW.values() for k in ks))

# ── Non-matching text ────────────────────────────────────────────────────────
check("unrelated headline does not match",
      not content_matches_keywords("LTA considering new Bus Rapid Transit for Tuas South"))
check("empty string does not match", not content_matches_keywords(""))

print()
print(f"{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
