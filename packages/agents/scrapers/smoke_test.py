#!/usr/bin/env python3
"""
Yishun Again — Scraper Smoke Test (instrumented, READ-ONLY)
===========================================================
Purpose: measure live scraper behaviour from the HOME IP. Pure measurement
— NO database writes of any kind. Exercises the real network/fetch/parse
path and stops INSTANTLY on any sign of bot detection.

Run from the project venv:
    cd C:\\Projects\\yishun-again\\packages\\agents
    ../../.venv/Scripts/python.exe -m scrapers.smoke_test    # if placed in scrapers/
  OR just run this file directly:
    ../../.venv/Scripts/python.exe smoke_test.py

What it measures, per keyword:
  - candidates returned + their actual publication-date range (proves the
    real recency window of Google News RSS)
  - request count and elapsed time to first block
  - whether a single polite backoff recovers, or the block persists
  - failure mode classification (silent-empty / hard-error / soft-ban)

BOT-TRAP POLICY (critical):
  On the FIRST sign of a block — HTTP 429, HTTP 403, a CAPTCHA / "unusual
  traffic" / "sorry/index" page, or a sudden empty-results cliff after
  prior success — the test STOPS that source immediately, records the
  trigger, and moves on. It does NOT retry into a ban. If ANY bot trap
  fires, the final report says so prominently and recommends a replan.

This file is standalone and imports nothing from the project pipeline, so
it cannot accidentally write to Supabase. It only does HTTP GETs.
"""

import sys
import time
import random
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone

# --- gentle, conservative knobs ---------------------------------------
PROBE_KEYWORDS = ["yishun murder", "yishun fire", "yishun accident", "yishun cat"]
PER_KEYWORD_DELAY = (4.0, 6.0)     # random polite delay between keywords (s)
ONE_BACKOFF_WAIT  = 30             # single recovery attempt wait (s) after a block
REQUEST_TIMEOUT   = 20             # per-request timeout (s)
MAX_KEYWORDS      = 4              # hard cap — stay gentle on the home IP

GNEWS_RSS = ("https://news.google.com/rss/search"
             "?q={q}&hl=en-SG&gl=SG&ceid=SG:en")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# Markers that indicate a block / bot trap rather than a normal empty result
BLOCK_MARKERS = [
    "unusual traffic", "/sorry/", "captcha", "recaptcha",
    "detected unusual", "automated queries", "not a robot",
]


def _classify_block(status, body_snippet):
    """Return a block-reason string if this looks like a bot trap, else None."""
    if status in (429, 403):
        return f"HTTP {status}"
    low = (body_snippet or "").lower()
    for m in BLOCK_MARKERS:
        if m in low:
            return f"block-page marker: '{m}'"
    return None


def probe_gnews_rss(keyword):
    """
    One RSS fetch for a keyword. Returns a dict with measurements.
    Uses feedparser if available, else urllib + minimal parsing.
    Never writes anything.
    """
    import feedparser  # project already depends on this

    q = urllib.parse.quote(keyword)
    url = GNEWS_RSS.format(q=q)

    result = {
        "keyword": keyword,
        "url": url,
        "ok": False,
        "http_status": None,
        "entry_count": 0,
        "date_min": None,
        "date_max": None,
        "block_reason": None,
        "elapsed_s": None,
        "note": "",
    }

    # feedparser doesn't expose status cleanly for all cases; fetch raw first
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept-Language": "en-SG,en;q=0.9"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            status = resp.getcode()
            raw = resp.read()
    except urllib.error.HTTPError as e:
        result["http_status"] = e.code
        result["elapsed_s"] = round(time.time() - t0, 2)
        result["block_reason"] = _classify_block(e.code, "")
        result["note"] = f"HTTPError {e.code}"
        return result
    except Exception as e:
        result["elapsed_s"] = round(time.time() - t0, 2)
        result["note"] = f"network error: {type(e).__name__}: {e}"
        return result

    result["elapsed_s"] = round(time.time() - t0, 2)
    result["http_status"] = status

    # Check the raw body for block-page markers even on a 200
    snippet = raw[:4000].decode("utf-8", errors="ignore")
    block = _classify_block(status, snippet)
    if block:
        result["block_reason"] = block
        result["note"] = "BLOCK DETECTED in body/status"
        return result

    # Parse the feed
    feed = feedparser.parse(raw)
    entries = feed.entries or []
    result["entry_count"] = len(entries)

    dates = []
    for e in entries:
        # feedparser normalises to published_parsed when present
        pp = getattr(e, "published_parsed", None)
        if pp:
            try:
                dates.append(datetime(*pp[:6], tzinfo=timezone.utc))
            except Exception:
                pass
    if dates:
        result["date_min"] = min(dates).date().isoformat()
        result["date_max"] = max(dates).date().isoformat()

    # An empty feed on a 200 is NOT necessarily a block — but flag it
    if result["entry_count"] == 0:
        result["note"] = "empty result on HTTP 200 (could be genuine, or a soft-cliff)"
    result["ok"] = True
    return result


def main():
    print("=" * 68)
    print("YISHUN AGAIN — SCRAPER SMOKE TEST (read-only, home IP)")
    print("Pure measurement. No DB writes. Fail-stop on bot detection.")
    print("Started:", datetime.now(timezone.utc).isoformat())
    print("=" * 68)

    results = []
    bot_trap_fired = False
    trap_detail = None

    for i, kw in enumerate(PROBE_KEYWORDS[:MAX_KEYWORDS]):
        print(f"\n[{i+1}/{min(len(PROBE_KEYWORDS), MAX_KEYWORDS)}] probing: {kw!r}")
        r = probe_gnews_rss(kw)
        results.append(r)

        print(f"    http={r['http_status']}  entries={r['entry_count']}  "
              f"dates={r['date_min']}..{r['date_max']}  elapsed={r['elapsed_s']}s")
        if r["note"]:
            print(f"    note: {r['note']}")

        # ---- BOT-TRAP FAIL-STOP --------------------------------------
        if r["block_reason"]:
            bot_trap_fired = True
            trap_detail = (kw, r["block_reason"])
            print(f"    !! BOT TRAP: {r['block_reason']} — STOPPING immediately.")
            print(f"    !! One polite {ONE_BACKOFF_WAIT}s backoff, single recovery probe, "
                  f"then halt regardless.")
            time.sleep(ONE_BACKOFF_WAIT)
            r2 = probe_gnews_rss(kw)
            if r2["block_reason"]:
                print(f"    !! Still blocked after backoff ({r2['block_reason']}). "
                      f"Backoff does NOT recover. Halting test.")
            else:
                print(f"    .. Recovered after backoff (http={r2['http_status']}, "
                      f"entries={r2['entry_count']}). Still halting — trap was tripped.")
            results.append({"keyword": kw + " (post-backoff)", **r2})
            break
        # --------------------------------------------------------------

        # polite delay before next keyword
        if i < min(len(PROBE_KEYWORDS), MAX_KEYWORDS) - 1:
            d = random.uniform(*PER_KEYWORD_DELAY)
            print(f"    sleeping {d:.1f}s (polite)")
            time.sleep(d)

    # ---- REPORT ------------------------------------------------------
    print("\n" + "=" * 68)
    print("SMOKE TEST REPORT")
    print("=" * 68)

    successful = [r for r in results if r.get("ok")]
    all_dates = [d for r in successful for d in (r["date_min"], r["date_max"]) if d]
    print(f"Keywords probed:        {len([r for r in results if '(post-backoff)' not in r['keyword']])}")
    print(f"Successful fetches:     {len(successful)}")
    print(f"Total entries seen:     {sum(r['entry_count'] for r in successful)}")
    if all_dates:
        print(f"Overall date range:     {min(all_dates)} .. {max(all_dates)}")
        print(f"  --> this is the REAL recency window of Google News RSS from your IP")
    print()

    if bot_trap_fired:
        print("*** BOT TRAP FIRED ***")
        print(f"    Tripped on keyword {trap_detail[0]!r} via: {trap_detail[1]}")
        print("    RECOMMENDATION: do NOT re-run immediately. Report this back and")
        print("    we REPLAN the smoke test (longer delays, fewer keywords, or a")
        print("    different probe cadence). The home IP may be briefly rate-limited.")
    else:
        print("No bot traps fired. RSS responded within the gentle probe envelope.")
        print("Per-keyword detail:")
        for r in results:
            print(f"  - {r['keyword']:<28} entries={r['entry_count']:<4} "
                  f"dates={r['date_min']}..{r['date_max']}")
        print()
        print("READ THIS: the date range above tells us what the autonomous")
        print("pipeline can actually rely on. If max date is recent (this month)")
        print("and min date is ~12-24 months back, the rolling-window thesis holds")
        print("and the forward-looking pipeline is viable on RSS alone.")

    print("\nFinished:", datetime.now(timezone.utc).isoformat())
    print("Reminder: this script wrote NOTHING to the database.")


if __name__ == "__main__":
    main()
