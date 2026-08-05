"""
Guard: a publication date read out of a URL path must come from a REAL date
segment, never from the leading digits of a slug.

`scrapers._URL_DATE_RE` used to end in `(?:/|\b)`. A word boundary sits between
a digit and a hyphen, so `mothership.sg/2026/07/6-men-charged-yishun-rioting/`
matched and resolved to 2026-07-06 — eighteen days before the incident it
described, and printed on the published page next to the link. Mothership puts
only /YYYY/MM/ in its paths; the day is not there to read, and the fetch rungs
in `resolve_published_at` are what find it.

Offline: exercises the regex and `_safe_date` only, no network.

Run: ./.venv/Scripts/python.exe test_url_date_extraction.py
"""

from scrapers import _URL_DATE_RE, _safe_date

failed = 0


def check(name: str, cond: bool) -> None:
    global failed
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")
    if not cond:
        failed += 1


def date_from_url(url: str):
    """The extraction step of resolve_published_at, isolated."""
    m = _URL_DATE_RE.search(url)
    return _safe_date(*m.groups()) if m else None


print("\n== dateless paths must NOT yield a date ==")

# The case that shipped a wrong date to production.
check("mothership /2026/07/6-men-charged- -> None",
      date_from_url("https://mothership.sg/2026/07/6-men-charged-yishun-rioting/") is None)
check("mothership /2018/02/2-cats-dead- -> None",
      date_from_url("https://mothership.sg/2018/02/2-cats-dead-yishun-abused/") is None)
check("mothership /2017/02/2-cats-found-dead- -> None",
      date_from_url("https://mothership.sg/2017/02/2-cats-found-dead-horrifically-abused-in-yishun/") is None)
# A slug that merely opens with digits, at any depth.
check("/2026/08/10km-run-yishun/ -> None",
      date_from_url("https://mothership.sg/2026/08/10km-run-yishun/") is None)
check("month-only path -> None",
      date_from_url("https://mothership.sg/2026/07/") is None)

print("\n== real /YYYY/MM/DD/ paths still resolve ==")

check("malaymail /2018/07/13/ -> 2018-07-13",
      str(date_from_url("https://www.malaymail.com/news/2018/07/13/some-story/1234")) == "2018-07-13")
check("trailing slash -> 2026-07-29",
      str(date_from_url("https://example.com/2026/07/29/")) == "2026-07-29")
check("no trailing slash, end of URL -> 2026-07-29",
      str(date_from_url("https://example.com/2026/07/29")) == "2026-07-29")
check("query string after the day -> 2026-07-29",
      str(date_from_url("https://example.com/2026/07/29?utm_source=x")) == "2026-07-29")
check("fragment after the day -> 2026-07-29",
      str(date_from_url("https://example.com/2026/07/29#top")) == "2026-07-29")
check("single-digit month and day -> 2026-7-9",
      str(date_from_url("https://example.com/2026/7/9/story")) == "2026-07-09")

print("\n== calendar-invalid dates are rejected, not rolled over ==")

check("/2026/02/31/ -> None", date_from_url("https://example.com/2026/02/31/") is None)
check("/2026/13/01/ -> None", date_from_url("https://example.com/2026/13/01/") is None)

print(f"\n{'ALL PASSED' if not failed else str(failed) + ' FAILED'}\n")
raise SystemExit(1 if failed else 0)
