"""
Self-contained tests for the Phase-1 source adapter port. No pytest, no network.
Run: .venv/Scripts/python.exe test_ingestion_sources.py

Phase 1 of issue #23: Mothership, Straits Times and Reddit joined CNA + Google
News RSS in the live pipeline. The prerequisite was `published_at` — without it
a candidate is dateless: it bypasses the recency watermark, is re-processed by
Stage 1/2 every pass, and cannot be approved until an operator sets a date by
hand (QA H3).
"""
import importlib

srcmod = importlib.import_module("ingestion.sources")
from ingestion.sources.legacy import LegacyScraperSource  # noqa: E402
from datetime import date  # noqa: E402

passed = failed = 0
def check(name, cond):
    global passed, failed
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    passed += 1 if cond else 0
    failed += 0 if cond else 1

print("ingestion source adapter tests:")

ROWS = [
    {"title": "A", "content": "c1", "url": "https://x/1", "source_name": "Mothership",
     "source_type": "msm", "published_at": date(2026, 7, 16)},
    {"title": "B", "content": "c2", "url": "https://x/2", "source_name": "Mothership",
     "source_type": "msm", "published_at": None},          # dateless
    {"title": "C", "content": "c3", "url": "",             "source_name": "Mothership",
     "source_type": "msm", "published_at": date(2026, 7, 1)},  # no url -> skipped
]
src = LegacyScraperSource("mothership", lambda: list(ROWS),
                          source_name="Mothership", source_type="msm")

cands = src.fetch(since=None)
check("drops rows with no url", len(cands) == 2)
check("maps title/content/url", cands[0].title == "A" and cands[0].url == "https://x/1")
check("passes published_at through", cands[0].published_at == date(2026, 7, 16))
check("keeps dateless as None (never infers a date)", cands[1].published_at is None)
check("stamps discovered_via with the source name", all(c.discovered_via == "mothership" for c in cands))
check("carries source_type", all(c.source_type == "msm" for c in cands))

# source_type override + per-item source_name (Reddit yields per-subreddit names)
rsrc = LegacyScraperSource("reddit", lambda: [
    {"title": "R", "content": "c", "url": "https://r/1",
     "source_name": "Reddit SingaporeRaw", "source_type": "reddit",
     "published_at": date(2026, 7, 9)},
], source_type="reddit")
rc = rsrc.fetch(since=None)
check("reddit: per-item source_name preserved", rc[0].source_name == "Reddit SingaporeRaw")
check("reddit: source_type = reddit", rc[0].source_type == "reddit")

# falls back to the configured source_name when the row omits one
nsrc = LegacyScraperSource("straits_times", lambda: [
    {"title": "S", "content": "c", "url": "https://s/1", "published_at": None},
], source_name="The Straits Times")
check("falls back to configured source_name", nsrc.fetch(since=None)[0].source_name == "The Straits Times")

# empty scrape is not an error (legacy scrapers return [] on both block and no-news)
check("empty scrape returns []", LegacyScraperSource("x", lambda: []).fetch(since=None) == [])

# ── the live registry ───────────────────────────────────────────────────────
names = [s.name for s in srcmod.get_enabled_sources()]
check("live registry has all Phase-1 + 2a sources",
      set(names) == {"cna", "mothership", "straits_times", "mustsharenews",
                     "the_independent", "yahoo", "google_news_rss", "reddit"})
check("dateless HTML scrapers stay unregistered (Phase 2b) and EDMW (Phase 3)",
      not ({"asiaone", "stomp", "zaobao", "shinmin", "beritaharian",
            "tamilmurasu", "edmw"} & set(names)))
check("every registered source satisfies the Source protocol",
      all(hasattr(s, "name") and hasattr(s, "enabled") and callable(getattr(s, "fetch", None))
          for s in srcmod.get_enabled_sources()))

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
