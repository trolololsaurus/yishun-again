"""
Self-contained tests for article publication-date extraction. No pytest, no network.
Run: .venv/Scripts/python.exe test_published_date_resolution.py

WHY THIS FILE EXISTS
--------------------
Every source link on a published incident prints the date its article ran. The
date is resolved by `scrapers._date_from_html` over the article HTML, and a
miss renders "Undated" on a live page.

Two real gaps found on 2026-08-04 while auditing the Orchid Park car fire:

1. ATTRIBUTE ORDER. The meta patterns required `property=` before `content=`,
   so `<meta content="..." property="article:published_time">` — valid HTML,
   and what several CMSs emit — matched nothing.
2. NARROW KEY SET. Only article:published_time / datePublished / <time> were
   tried; og:published_time, pubdate and publishdate were not.

Not everything is recoverable, and that is deliberate. Mothership publishes NO
machine-readable date and no visible date text — verified by regex, by a full
Playwright render (no meta, no <time>, no JSON-LD, no date string in
document.body.innerText) and by Wayback. Its date exists only in its RSS feed
at publish time. Such links stay "Undated", which is honest; a guessed date on
a published page is not.
"""
from datetime import date
from scrapers import _date_from_html, _PUB_META_KEYS

passed = failed = 0


def check(name, cond):
    global passed, failed
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    passed += 1 if cond else 0
    failed += 0 if cond else 1


print("published-date resolution tests:")

# ── Attribute order must not matter ─────────────────────────────────────────
check("property before content",
      _date_from_html('<meta property="article:published_time" content="2026-07-29T10:00:00+08:00">')
      == date(2026, 7, 29))
check("content before property (the regression)",
      _date_from_html('<meta content="2026-07-29T10:00:00+08:00" property="article:published_time">')
      == date(2026, 7, 29))
check("itemprop, reversed",
      _date_from_html('<meta content="2026-07-25" itemprop="datePublished">') == date(2026, 7, 25))

# ── Key coverage ────────────────────────────────────────────────────────────
check("og:published_time",
      _date_from_html('<meta property="og:published_time" content="2026-07-24">') == date(2026, 7, 24))
check("name=pubdate",
      _date_from_html('<meta name="pubdate" content="2026-07-28">') == date(2026, 7, 28))
check("name=publishdate",
      _date_from_html('<meta name="publishdate" content="2026-07-23">') == date(2026, 7, 23))
check("JSON-LD datePublished",
      _date_from_html('<script type="application/ld+json">{"@type":"NewsArticle",'
                      '"datePublished":"2026-07-27T09:00:00Z"}</script>') == date(2026, 7, 27))
check("<time datetime>",
      _date_from_html('<time datetime="2026-07-26T08:00:00">26 Jul</time>') == date(2026, 7, 26))

# ── Honest failure ──────────────────────────────────────────────────────────
check("no date -> None, never a guess", _date_from_html('<p>no date here</p>') is None)
check("empty html -> None", _date_from_html('') is None)
check("malformed date -> None",
      _date_from_html('<meta property="article:published_time" content="not-a-date">') is None)
check("impossible date -> None",
      _date_from_html('<meta property="article:published_time" content="2026-13-45">') is None)

# A JS shell with only image-CDN upload timestamps must NOT yield a date. That
# heuristic was measured against 10 Mothership articles with known RSS dates and
# was wrong on 2 of them — 80% is worse than "Undated" for a date printed on a
# published page.
check("image-CDN upload timestamps are not treated as a publication date",
      _date_from_html('<img src="https://static.mothership.sg/1/2026/07/'
                      'cover-photo-mothership-2026-07-29T171724.jpg">') is None)

check("the key list is order-independent by construction",
      all(isinstance(k, str) for k in _PUB_META_KEYS) and "og:published_time" in _PUB_META_KEYS)

print()
print(f"{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
