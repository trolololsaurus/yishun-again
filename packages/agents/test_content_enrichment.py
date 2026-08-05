"""
Self-contained tests for feed-content enrichment. No pytest, no network.
Run: .venv/Scripts/python.exe test_content_enrichment.py

WHY THIS FILE EXISTS
--------------------
Stage 2 writes the incident summary from `content`. Several publishers put only
a STANDFIRST in their RSS <description>, not the article. Measured live on
2026-08-05 over the first 6 entries of each feed:

    straits_times     67-110 chars      one line
    cna               64-175 chars      one line
    yahoo             0 chars           nothing at all
    mothership        1372-4910 chars   full body
    mustsharenews     3031-7939 chars   full body
    the_independent   2396-4482 chars   full body

So every Straits Times, CNA and Yahoo incident was drafted from a headline plus
one sentence. The PMD-impound story reached the War Room with 108 characters of
source text behind it, produced a visibly thin card, and scored 0.30 confidence.
Enrichment fetches the article when the feed is that thin — measured on that
exact URL: 108 -> 3379 chars.

The invariant that matters: enrichment NEVER SHRINKS the text. A failed fetch
must leave the feed's own words in place, because a thin summary is bad and an
empty one is worse.
"""
from unittest import mock

import scrapers
from scrapers import enrich_thin_content, MIN_BODY_CHARS

passed = failed = 0


def check(name, cond):
    global passed, failed
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    passed += 1 if cond else 0
    failed += 0 if cond else 1


def _item(content, url="https://www.straitstimes.com/a"):
    return {"url": url, "title": "t", "content": content}


print("content enrichment tests:")

# ── Thin feed content is replaced by the article body ───────────────────────
with mock.patch.object(scrapers, "article_body", return_value="B" * 3000):
    out = enrich_thin_content(_item("Only those with a valid CMN may use PMAs."))
check("a standfirst is replaced by the fetched body", len(out["content"]) == 3000)

with mock.patch.object(scrapers, "article_body", return_value="B" * 3000):
    out = enrich_thin_content(_item(""))
check("empty feed content (Yahoo) is enriched", len(out["content"]) == 3000)

# ── Already-good content is left alone, and NOT re-fetched ──────────────────
fat = "A" * (MIN_BODY_CHARS + 50)
with mock.patch.object(scrapers, "article_body") as ab:
    out = enrich_thin_content(_item(fat))
check("full-body feeds are untouched", out["content"] == fat)
check("...and no fetch is issued for them", ab.call_count == 0)

# ── NEVER shrink ────────────────────────────────────────────────────────────
with mock.patch.object(scrapers, "article_body", return_value=""):
    out = enrich_thin_content(_item("short but real"))
check("a failed fetch keeps the feed's own text", out["content"] == "short but real")

with mock.patch.object(scrapers, "article_body", return_value="tiny"):
    out = enrich_thin_content(_item("this is longer than the fetch returned"))
check("a shorter fetch result is rejected",
      out["content"] == "this is longer than the fetch returned")

# ── Robustness ──────────────────────────────────────────────────────────────
with mock.patch.object(scrapers, "article_body", side_effect=RuntimeError("boom")):
    try:
        out = enrich_thin_content(_item("short"))
        raised = False
    except Exception:
        raised = True
check("a raising fetch does not propagate", not raised and out["content"] == "short")

with mock.patch.object(scrapers, "article_body", return_value="B" * 3000) as ab:
    out = enrich_thin_content({"url": "", "content": "short"})
check("no url -> no fetch, item unchanged", out["content"] == "short")

with mock.patch.object(scrapers, "article_body", return_value="B" * 3000):
    src = _item("short")
    out = enrich_thin_content(src)
check("the input dict is not mutated in place", src["content"] == "short")

check("threshold is high enough to catch a standfirst", MIN_BODY_CHARS > 200)

# ── The adapters must actually call it, or none of the above matters ────────
import inspect
from ingestion.sources import legacy
from ingestion.sources.msm import cna
check("LegacyScraperSource enriches (covers 11 scrapers)",
      "enrich_thin_content" in inspect.getsource(legacy))
check("CNASource enriches (the 12th)",
      "enrich_thin_content" in inspect.getsource(cna))

print()
print(f"{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
