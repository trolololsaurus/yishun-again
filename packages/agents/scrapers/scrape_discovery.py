"""
Source discovery agent — runs first Monday of every month (spec §4.5).

Method: Parse Google News RSS for 'yishun singapore', extract unique article
domains, cross-reference against the known-sources allowlist, and surface
novel domains as candidate sources for operator review.

Output: list of candidate dicts — {name, url, type, notes}
  - These are logged for operator review in War Room ("New Sources" tab)
  - Nothing is scraped until operator sets approved_by_operator = TRUE

Public API
----------
discover() -> list[dict]
    Pure-ish: fetches the feed, returns candidates. No DB.
run(supabase_client=None) -> dict
    discover() + persist. Called by ops/daily.py on the first Monday of the
    month. Returns {found, inserted, skipped, errors}.
"""

import json
import logging
import sys
import time
from urllib.parse import urlparse

import feedparser
import httpx

from . import BROWSER_HEADERS

logger = logging.getLogger(__name__)

# Google News RSS — free, no API key required.
_GNEWS_RSS = (
    "https://news.google.com/rss/search"
    "?q=yishun+singapore&hl=en-SG&gl=SG&ceid=SG:en"
)

# Domains already tracked — candidates matching these are skipped.
_KNOWN_DOMAINS = {
    "channelnewsasia.com", "cna.asia",
    "straitstimes.com",
    "mothership.sg",
    "stomp.straitstimes.com",
    "mustsharenews.com",
    "theindependent.sg",
    "sg.news.yahoo.com", "yahoo.com",
    "asiaone.com",
    "zaobao.com.sg",
    "shinmin.sg",
    "beritaharian.sg",
    "tamilmurasu.com.sg",
    "reddit.com",
    "forums.hardwarezone.com.sg",
    "en.wikipedia.org",
}

# Domains to ignore even if novel (noise, paywalls, irrelevant aggregators)
_BLOCKLIST_DOMAINS = {
    "google.com", "facebook.com", "twitter.com", "x.com",
    "tiktok.com", "instagram.com", "youtube.com",
    "propertyguru.com.sg", "99.co", "srx.com.sg",
    "carousell.com", "lazada.sg", "shopee.sg",
}


def _extract_domain(url: str) -> str:
    try:
        parsed = urlparse(url)
        host = parsed.netloc.lower().lstrip("www.")
        return host
    except Exception:
        return ""


def discover() -> list[dict]:
    """
    Search Google News for Yishun coverage and return novel source candidates.

    Returns:
        List of {name, url, type, notes} dicts for operator review.
    """
    candidates: list[dict] = []
    seen_domains: set[str] = set()

    try:
        feed = feedparser.parse(_GNEWS_RSS)

        if feed.bozo and not feed.entries:
            logger.warning("Discovery: Google News RSS parse error: %s", feed.bozo_exception)
            return candidates

        for entry in feed.entries:
            url = entry.get("link", "").strip()
            if not url:
                continue

            domain = _extract_domain(url)
            if not domain:
                continue
            if domain in seen_domains:
                continue
            if domain in _KNOWN_DOMAINS:
                continue
            if domain in _BLOCKLIST_DOMAINS:
                continue
            if any(known in domain for known in _KNOWN_DOMAINS):
                continue

            seen_domains.add(domain)
            title = entry.get("title", "").strip()

            candidates.append({
                "name":  domain,
                "url":   f"https://{domain}",
                "type":  "msm",  # default — operator confirms or corrects
                "notes": f"Discovered via Google News: '{title[:120]}'",
            })

    except Exception as exc:
        logger.error("Discovery agent error: %s", exc)

    logger.info("Discovery: %d novel candidate source(s) found", len(candidates))
    return candidates


def run(supabase_client=None) -> dict:
    """
    Discover novel outlets and file them for operator review.

    Returns {found, inserted, skipped, errors}. Never raises — it runs inside
    the unattended daily chain.

    Rows are written `approved_by_operator=False` AND `is_active=False`. Both
    matter and they mean different things: `approved_by_operator` is what
    classifiers/source_allowlist checks before a URL may be cited, and
    `is_active` is the "we scrape this" flag whose default is TRUE. A candidate
    nobody has looked at yet must be neither, and inheriting the active default
    would list an unvetted domain alongside the real fleet in War Room.
    """
    stats = {"found": 0, "inserted": 0, "skipped": 0, "errors": 0}

    try:
        candidates = discover()
    except Exception as exc:                      # noqa: BLE001
        logger.error("Discovery: feed pass failed: %s", exc)
        return {**stats, "errors": 1}

    stats["found"] = len(candidates)
    if not candidates:
        return stats

    if supabase_client is None:
        try:
            from classifiers.corroboration import get_supabase_client
            supabase_client = get_supabase_client()
        except Exception as exc:                  # noqa: BLE001
            logger.error("Discovery: Supabase not configured: %s", exc)
            return {**stats, "errors": 1}

    for candidate in candidates:
        try:
            supabase_client.table("sources").insert({
                "name":                    candidate["name"],
                "url":                     candidate["url"],
                "type":                    candidate.get("type", "msm"),
                "approved_by_operator":    False,
                "is_active":               False,
                "discovery_notes":         candidate.get("notes", ""),
                "scrape_interval_minutes": 0,
            }).execute()
            stats["inserted"] += 1
            logger.info("Discovery: candidate filed — %s", candidate["name"])
        except Exception as exc:                  # noqa: BLE001
            # `name` is UNIQUE, so the overwhelmingly common failure here is
            # "we have seen this domain before" — expected every month, not an
            # error worth waking anyone for.
            stats["skipped"] += 1
            logger.debug("Discovery: candidate skipped (%s): %s", candidate["name"], exc)

    logger.info(
        "Discovery complete — found=%d inserted=%d skipped=%d",
        stats["found"], stats["inserted"], stats["skipped"],
    )
    return stats


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    items = discover()
    print(f"\nSource Discovery — {len(items)} candidate(s) found.")
    if items:
        for c in items:
            print(f"  {c['name']} — {c['url']}")
            print(f"    Notes: {c['notes']}")
    else:
        print("No new source candidates found.")
    sys.exit(0)
