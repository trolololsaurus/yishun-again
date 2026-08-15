"""
Self-contained tests for the discovery adapters that replaced Google News RSS
(no pytest, no network). Run: .venv/Scripts/python.exe test_news_sitemap.py

Covers the pure parse helpers in ingestion/sources/news_sitemap.py against
captured fixtures, plus the registry invariant that matters most: every
configured discovery URL points at a PUBLISHER, never an aggregator or a
redirect wrapper. That invariant is the whole reason these adapters exist —
see the module docstring in news_sitemap.py.
"""
import ingestion.sources.news_sitemap as ns
import ingestion.sources.wp_search as wp
import classifiers.source_allowlist as sa
from datetime import date

passed = failed = 0


def check(name, cond):
    global passed, failed
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    passed += 1 if cond else 0
    failed += 0 if cond else 1


print("news sitemap / wp search tests:")

# ── parse_sitemap: the shape SG publishers actually serve ────────────────────
SITEMAP = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
  <url>
    <loc>https://www.straitstimes.com/singapore/fire-in-yishun-flat</loc>
    <news:news>
      <news:publication><news:name>ST</news:name><news:language>en</news:language></news:publication>
      <news:publication_date>2026-07-30T14:05:00+08:00</news:publication_date>
      <news:title>Fire breaks out in Yishun flat</news:title>
    </news:news>
  </url>
  <url>
    <loc>https://www.straitstimes.com/world/some-unrelated-story</loc>
    <news:news>
      <news:publication_date>2026-07-31T09:00:00+08:00</news:publication_date>
      <news:title>Something else entirely</news:title>
    </news:news>
  </url>
  <url>
    <loc>https://www.straitstimes.com/singapore/khatib-mrt-delay-2288411</loc>
    <news:news>
      <news:publication_date>2026-08-01T07:00:00+08:00</news:publication_date>
    </news:news>
  </url>
</urlset>"""

rows = ns.parse_sitemap(SITEMAP)
check("parses every <url> entry", len(rows) == 3)
check("extracts the canonical loc",
      rows[0][0] == "https://www.straitstimes.com/singapore/fire-in-yishun-flat")
check("extracts news:title", rows[0][1] == "Fire breaks out in Yishun flat")
check("parses news:publication_date with a tz offset", rows[0][2] == date(2026, 7, 30))

# CNA served 50 locs but only 35 <news:title> on 2026-08-02, so a missing title
# must not lose the entry — the slug carries the keyword.
check("entry with no news:title still parsed", rows[2][1])
check("title falls back to the URL slug", "khatib" in rows[2][1].lower())
check("slug fallback strips the trailing article id",
      not rows[2][1].rstrip().endswith("2288411"))

# ── _title_from_slug ─────────────────────────────────────────────────────────
check("slug -> readable title",
      ns._title_from_slug("https://x.sg/a/fire-in-yishun-flat") == "Fire in yishun flat")
check("slug strips .html", "html" not in ns._title_from_slug("https://x.sg/a/car-fire.html").lower())
check("trailing slash tolerated",
      ns._title_from_slug("https://x.sg/a/car-fire/") == "Car fire")
check("empty url -> empty title", ns._title_from_slug("") == "")

# ── _parse_pub_date ──────────────────────────────────────────────────────────
check("ISO with Z", ns._parse_pub_date("2026-08-02T00:00:00Z") == date(2026, 8, 2))
check("ISO with offset", ns._parse_pub_date("2026-08-02T08:00:00+08:00") == date(2026, 8, 2))
check("bare date", ns._parse_pub_date("2026-08-02") == date(2026, 8, 2))
check("None -> None", ns._parse_pub_date(None) is None)
check("garbage -> None", ns._parse_pub_date("not a date") is None)
check("impossible date -> None", ns._parse_pub_date("2026-13-45") is None)

# ── malformed input must degrade, not explode ────────────────────────────────
try:
    ns.parse_sitemap(b"<urlset><url><loc>")
    check("truncated XML raises SourceUnavailableError", False)
except Exception as exc:
    check("truncated XML raises SourceUnavailableError",
          type(exc).__name__ == "SourceUnavailableError")

check("empty urlset -> no rows",
      ns.parse_sitemap(b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"/>') == [])

# ── THE registry invariant ───────────────────────────────────────────────────
# google_news_rss was removed because its URLs were wrappers, not articles. The
# replacement is only an improvement if every configured endpoint is a
# publisher's own domain. Assert that mechanically rather than by eyeball.
for name, source_name, url in ns.NEWS_SITEMAPS:
    check(f"{name} points at a publisher, not a redirector",
          not sa.is_redirect_domain(url))
    check(f"{name} is https", url.startswith("https://"))

for name, source_name, base in wp.WP_SEARCH_SITES:
    check(f"{name} points at a publisher, not a redirector",
          not sa.is_redirect_domain(base))

check("no sitemap URL mentions google news",
      not any("news.google" in u for _, _, u in ns.NEWS_SITEMAPS))

# Source ids must be unique across both registries and distinct from the
# scrapers', because scraper_health and pipeline_state are keyed on them — two
# sources sharing an id would interleave their watermarks.
ids = [n for n, _, _ in ns.NEWS_SITEMAPS] + [n for n, _, _ in wp.WP_SEARCH_SITES]
check("discovery source ids are unique", len(ids) == len(set(ids)))

import ingestion.sources as srcmod
all_names = [s.name for s in srcmod.get_enabled_sources()]
check("no duplicate source ids in the live registry",
      len(all_names) == len(set(all_names)))

# ── WordPressSearchSource URL construction ───────────────────────────────────
s = wp.WordPressSearchSource("t", "T", "https://example.sg/")
check("feed_url uses the WP search-feed form",
      s.feed_url("yishun") == "https://example.sg/?s=yishun&feed=rss2")
check("subzone term is searched too (khatib in the term list)",
      "khatib" in s.terms)
check("multi-word term is url-encoded",
      s.feed_url("chong pang") == "https://example.sg/?s=chong%20pang&feed=rss2")
check("trailing slash on base_url is normalised", "//?s=" not in s.feed_url("yishun"))

print()
print(f"{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
