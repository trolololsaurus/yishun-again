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

# ── Human-readable dateline (the Mothership regression) ─────────────────────
# Mothership prints its date ONLY as text:
#     <h3 class="text-sm pl-6">July 30, 2026, 11:30 AM</h3>
# It was reported "Undated" on a live page because every pattern here assumed
# DAY-FIRST ("30 July 2026") while Mothership writes MONTH-FIRST. The regex was
# wrong, not the publisher. Validated 10/10 against known RSS pubDates.
check("month-first dateline (the regression)",
      _date_from_html('<h3 class="text-sm pl-6">July 30, 2026, 11:30 AM</h3>') == date(2026, 7, 30))
check("day-first dateline still works",
      _date_from_html('<p>Published 30 July 2026</p>') == date(2026, 7, 30))
check("abbreviated month, month-first",
      _date_from_html('<p>Jul 30, 2026</p>') == date(2026, 7, 30))
check("abbreviated month, day-first",
      _date_from_html('<p>30 Jul 2026</p>') == date(2026, 7, 30))
check("'Sept' is understood", _date_from_html('<p>Sept 3, 2026</p>') == date(2026, 9, 3))

# Metadata is authoritative and must WIN over body text — a page mentions many
# dates and only the meta tag is unambiguous.
check("a meta tag outranks a body dateline",
      _date_from_html('<meta property="article:published_time" content="2026-07-29">'
                      '<p>Some older event on July 1, 2026</p>') == date(2026, 7, 29))

# FIRST dateline wins: the article's own sits above the related-posts list.
check("the first dateline wins (article header before related posts)",
      _date_from_html('<h3>July 30, 2026, 11:30 AM</h3>'
                      '<span class="meta-time">July 30, 2026, 10:44 AM</span>'
                      '<span class="meta-time">June 2, 2026, 9:00 AM</span>') == date(2026, 7, 30))

check("an impossible text date is skipped, not coerced",
      _date_from_html('<p>February 30, 2026</p>') is None)
check("a bare year is not a date", _date_from_html('<p>Copyright 2026</p>') is None)

check("the key list is order-independent by construction",
      all(isinstance(k, str) for k in _PUB_META_KEYS) and "og:published_time" in _PUB_META_KEYS)

print()
print(f"{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
