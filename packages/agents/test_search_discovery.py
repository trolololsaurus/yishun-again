"""Offline guards for tools/search_discovery.py — the pure query/decode/filter/
manifest logic. No network (the DDG call is not exercised here)."""
import os
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tools.search_discovery import (
    parse_years, build_queries, _ddg_result_url, filter_links, to_manifest,
)

passed = failed = 0
def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"FAIL: {name}")

# ── parse_years ──────────────────────────────────────────────────────────────
check("range expands inclusive", parse_years("2017-2019") == [2017, 2018, 2019])
check("comma list", parse_years("2018,2020") == [2018, 2020])
check("single year", parse_years("2019") == [2019])
check("dedupe + sort", parse_years("2020,2018-2019,2018") == [2018, 2019, 2020])

# ── build_queries ────────────────────────────────────────────────────────────
qs = build_queries(["yishun"], [2018], None)
check("one query per (topic, year)", len(qs) == 1)
check("year is in the query text (DDG has no exact date sort)", qs[0] == "yishun 2018")
check("cartesian topic x year", len(build_queries(["a", "b"], [2017, 2018], None)) == 4)
sq = build_queries(["yishun"], [2018], ["mothership.sg"])
check("site: scoping appended", sq[0] == "yishun 2018 site:mothership.sg")

# ── _ddg_result_url: MUST decode DDG's redirect wrapper to the publisher URL ──
real = "https://mothership.sg/2018/09/empty-bullet-cartridge-orchid-park-secondary-yishun/"
wrapped = "//duckduckgo.com/l/?uddg=" + urllib.parse.quote(real, safe="")
check("decodes //duckduckgo.com/l/?uddg= wrapper to the real URL",
      _ddg_result_url(wrapped) == real)
check("passes a direct https link through unchanged",
      _ddg_result_url(real) == real)
check("empty href -> empty", _ddg_result_url("") == "")

# ── filter_links ─────────────────────────────────────────────────────────────
raw = [
    {"link": "https://mothership.sg/2018/09/empty-bullet-cartridge-orchid-park-secondary-yishun/",
     "title": "Empty bullet cartridge at Yishun school", "snippet": "..."},
    {"link": "https://stomp.sg/some-other-town-story", "title": "Bedok stabbing", "snippet": "no match"},
    {"link": "https://news.google.com/rss/articles/CBMiblob", "title": "Yishun something", "snippet": "wrapper"},
    {"link": "https://www.reddit.com/r/singapore/yishun_thread", "title": "Yishun crocodile", "snippet": "forum"},
    {"link": "https://www.facebook.com/MothershipSG/videos/123", "title": "Yishun video", "snippet": "social noise"},
    {"link": "https://www.youtube.com/watch?v=abc", "title": "Yishun clip", "snippet": "video noise"},
    {"link": "https://en.wikipedia.org/wiki/Yishun", "title": "Yishun", "snippet": "reference"},
    {"link": "https://www.nlb.gov.sg/yishun-history", "title": "Yishun archive", "snippet": "gov"},
    {"link": "https://www.sgtestpaper.com/sec4_Yishun_Town.html", "title": "Yishun exam paper", "snippet": "junk"},
    {"link": "https://secretsingapore.co/crocodile-spotted-at-yishun-dam-singapore/",
     "title": "Crocodile at Yishun Dam", "snippet": "..."},
    {"link": "https://secretsingapore.co/crocodile-spotted-at-yishun-dam-singapore/",
     "title": "dupe", "snippet": "yishun"},
]
kept = filter_links(raw)
urls = [k["url"] for k in kept]
check("keeps Yishun publisher links", any("mothership.sg/2018/09" in u for u in urls))
check("keeps unapproved blog (operator reviews it downstream)",
      any("secretsingapore.co" in u for u in urls))
check("drops non-Yishun result", not any("some-other-town" in u for u in urls))
check("drops google redirect wrapper", not any("news.google.com" in u for u in urls))
check("drops reddit signal host", not any("reddit.com" in u for u in urls))
check("drops facebook social noise", not any("facebook.com" in u for u in urls))
check("drops youtube video noise", not any("youtube.com" in u for u in urls))
check("drops wikipedia reference", not any("wikipedia.org" in u for u in urls))
check("drops .gov.sg archive", not any("gov.sg" in u for u in urls))
check("drops exam-paper junk", not any("sgtestpaper" in u for u in urls))
check("dedupes identical URLs",
      urls.count("https://secretsingapore.co/crocodile-spotted-at-yishun-dam-singapore/") == 1)

# ── to_manifest ──────────────────────────────────────────────────────────────
man = to_manifest(kept, route="queue")
check("one manifest entry per link", len(man) == len(kept))
check("each entry is one-URL, one-story", all(len(e["urls"]) == 1 for e in man))
check("route carried through", all(e["route"] == "queue" for e in man))
check("story falls back to title", man[0]["story"])

print()
print(f"{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
