"""
Self-contained tests for the Phase-1 source adapter port. No pytest, no network.
Run: .venv/Scripts/python.exe test_ingestion_sources.py

Phase 1 of issue #23: Mothership, Straits Times and Reddit joined CNA in the
live pipeline. The prerequisite was `published_at` — without it a candidate is
dateless: it bypasses the recency watermark, is re-processed by Stage 1/2 every
pass, and cannot be approved until an operator sets a date by hand (QA H3).

Google News RSS was also part of that original registry. It was removed on
2026-08-02 and replaced by the news-sitemap and WordPress-search discovery
adapters; the registry assertions below pin both halves of that change.
"""
import importlib

srcmod = importlib.import_module("ingestion.sources")
import classifiers.source_allowlist as sa  # noqa: E402
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

# source_type override + per-item source_name (Reddit yields per-subreddit names).
# Reddit is a SIGNAL now: scrape_reddit emits 'signal', so the candidate is
# signal-tier (never a quoted source, never the event date) while source_name
# still carries the subreddit for the operator.
rsrc = LegacyScraperSource("reddit", lambda: [
    {"title": "R", "content": "c", "url": "https://reddit.com/r/singapore/1",
     "source_name": "Reddit SingaporeRaw", "source_type": "signal",
     "published_at": date(2026, 7, 9)},
], source_type="signal")
rc = rsrc.fetch(since=None)
check("reddit: per-item source_name preserved", rc[0].source_name == "Reddit SingaporeRaw")
check("reddit: source_type = signal (forum signal, not a source)", rc[0].source_type == "signal")

# falls back to the configured source_name when the row omits one
nsrc = LegacyScraperSource("straits_times", lambda: [
    {"title": "S", "content": "c", "url": "https://s/1", "published_at": None},
], source_name="The Straits Times")
check("falls back to configured source_name", nsrc.fetch(since=None)[0].source_name == "The Straits Times")

# empty scrape is not an error (legacy scrapers return [] on both block and no-news)
check("empty scrape returns []", LegacyScraperSource("x", lambda: []).fetch(since=None) == [])

# ── the live registry ───────────────────────────────────────────────────────
names = [s.name for s in srcmod.get_enabled_sources()]

# The 14 scrapers (Phases 1-3). None of these was touched when Google News RSS
# was removed on 2026-08-02 — assert the whole set stays registered, so a future
# "cleanup" cannot quietly drop a publisher.
SCRAPER_SOURCES = {"cna", "mothership", "straits_times", "mustsharenews",
                   "the_independent", "yahoo", "asiaone", "stomp", "zaobao",
                   "shinmin", "berita_harian", "tamil_murasu",
                   "reddit", "edmw"}
check("all 14 scrapers still registered", SCRAPER_SOURCES <= set(names))

# Discovery adapters that replaced Google News RSS.
DISCOVERY_SOURCES = {
    "cna_sitemap", "straits_times_sitemap", "yahoo_sitemap", "asiaone_sitemap",
    "stomp_sitemap", "zaobao_sitemap", "berita_harian_sitemap",
    "tamil_murasu_sitemap", "the_independent_sitemap",
    "mustsharenews_search", "the_independent_search",
}
check("discovery adapters registered", DISCOVERY_SOURCES <= set(names))
check("registry is exactly scrapers + discovery",
      set(names) == SCRAPER_SOURCES | DISCOVERY_SOURCES)

# Guardrail: Google News RSS is gone and must not come back. Its wrappers are
# not article URLs, they broke dedupe, and they put a redirect where a citation
# belongs (see ingestion/sources/news_sitemap.py).
check("no Google News source is registered",
      not any("google" in n for n in names))
check("no aggregator/redirect source is registered",
      not any(sa.is_redirect_domain(getattr(s, "sitemap_url", "") or
                                    getattr(s, "base_url", "") or "https://example.com")
              for s in srcmod.get_enabled_sources()))
check("EDMW is registered as a SIGNAL source (never a quoted source)",
      any(s.name == "edmw" and s.source_type == "signal"
          for s in srcmod.get_enabled_sources()))
check("Reddit is registered as a SIGNAL source (July 2026 — UGC, not a source)",
      any(s.name == "reddit" and s.source_type == "signal"
          for s in srcmod.get_enabled_sources()))
# Declared on the Source itself, so a source whose fetch RAISES can still be
# typed in its scraper_health row (ingestion/health.py).
check("every registered source declares a source_type",
      all(getattr(s, "source_type", None) for s in srcmod.get_enabled_sources()))

# End-to-end: the reddit scraper emits the signal type and guardrail #2 catches
# it, so a reddit URL can never reach source_urls and its post date is never an
# incident date.
from scrapers import scrape_reddit  # noqa: E402
from classifiers.source_allowlist import is_signal_source, canonical_source_type  # noqa: E402
check("reddit scraper emits the signal type", scrape_reddit.SOURCE_TYPE == "signal")
check("reddit is recognised as signal by is_signal_source (guardrail #2)",
      is_signal_source(scrape_reddit.SOURCE_TYPE, "https://www.reddit.com/r/singapore/x") is True)
check("reddit type canonicalises to signal", canonical_source_type(scrape_reddit.SOURCE_TYPE) == "signal")
check("source names are unique (each keys its own pipeline_state watermark)",
      len(names) == len(set(names)))
check("every registered source satisfies the Source protocol",
      all(hasattr(s, "name") and hasattr(s, "enabled") and callable(getattr(s, "fetch", None))
          for s in srcmod.get_enabled_sources()))

# ── failure signalling: a dead source must not look like "no news" ──────────
from ingestion.contracts import SourceBlockedError, SourceUnavailableError  # noqa: E402
from scrapers import ScraperBlocked, ScraperError, raise_scrape_failure  # noqa: E402

def _raiser(exc):
    def _f():
        raise exc
    return LegacyScraperSource("x", _f)

def _raises(src, want):
    try:
        src.fetch(since=None)
        return False
    except Exception as e:
        return isinstance(e, want)

check("ScraperBlocked -> SourceBlockedError",
      _raises(_raiser(ScraperBlocked("429")), SourceBlockedError))
check("ScraperError -> SourceUnavailableError",
      _raises(_raiser(ScraperError("boom")), SourceUnavailableError))
check("unclassified exception -> SourceUnavailableError (not swallowed)",
      _raises(_raiser(RuntimeError("surprise")), SourceUnavailableError))
check("empty result is NOT an error (genuinely no Yishun news)",
      LegacyScraperSource("x", lambda: []).fetch(since=None) == [])

def _classify(text):
    try:
        raise_scrape_failure("S", Exception(text))
    except ScraperBlocked:
        return "blocked"
    except ScraperError:
        return "error"

check("429 classified as blocked",        _classify("HTTP 429 Too Many Requests") == "blocked")
check("403 classified as blocked",        _classify("403 Forbidden") == "blocked")
check("captcha/bot page -> blocked",      _classify("detected unusual traffic") == "blocked")
check("timeout -> transient error",       _classify("ConnectTimeout") == "error")
check("404 -> transient error (not a block)", _classify("404 Not Found") == "error")
check("raise_scrape_failure always raises",  _classify("anything") in {"blocked", "error"})


# ── resolve_published_at: the Phase-2b date helper (no network) ─────────────
import io  # noqa: E402
from contextlib import contextmanager  # noqa: E402
from unittest import mock  # noqa: E402
import scrapers as scrapers_pkg  # noqa: E402

@contextmanager
def fake_html(body: str | None):
    """Patch urlopen to serve `body`, or raise when body is None."""
    class _Resp:
        def read(self, *_a): return body.encode("utf-8")
        def __enter__(self): return self
        def __exit__(self, *a): return False
    def _open(*_a, **_k):
        if body is None:
            raise OSError("connection refused")
        return _Resp()
    with mock.patch.object(scrapers_pkg.urllib.request, "urlopen", _open):
        yield

from datetime import date as _d  # noqa: E402
rpa = scrapers_pkg.resolve_published_at

# 1. Date in the URL path — resolved with NO request at all.
with fake_html(None):   # any fetch would raise, proving none happens
    check("URL-path date resolved without fetching",
          rpa("https://mothership.sg/2026/07/16/some-story/") == _d(2026, 7, 16))

# 2. Meta tags
with fake_html('<meta property="article:published_time" content="2026-07-11T18:03:00+08:00">'):
    check("article:published_time parsed", rpa("https://x.example/story") == _d(2026, 7, 11))
with fake_html('{"datePublished":"2026-06-30T09:00:00Z"}'):
    check("JSON-LD datePublished parsed", rpa("https://x.example/story") == _d(2026, 6, 30))
with fake_html('<time datetime="2026-05-03">3 May</time>'):
    check("<time datetime> parsed", rpa("https://x.example/story") == _d(2026, 5, 3))

# 3. Failure modes never raise — they yield None (dateless -> routed to review)
with fake_html("<html><body>no date anywhere</body></html>"):
    check("no date in page -> None", rpa("https://x.example/story") is None)
with fake_html(None):
    check("fetch failure -> None (never raises)", rpa("https://x.example/story") is None)
check("empty url -> None", rpa("") is None)
with fake_html('<meta property="article:published_time" content="not-a-date">'):
    check("unparseable date -> None", rpa("https://x.example/story") is None)
with fake_html('<meta property="article:published_time" content="2026-13-45">'):
    check("out-of-range date -> None (no crash)", rpa("https://x.example/story") is None)

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
