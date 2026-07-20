"""
Source discovery agent — runs first Monday of every month (spec §4.5).

Method: Parse Google News RSS for 'yishun singapore', extract unique article
domains, cross-reference against the known-sources allowlist, and surface
novel domains as candidate sources for operator review.

Output: list of candidate dicts — {name, url, type, notes}
  - These are logged for operator review in War Room ("New Sources" tab)
  - Nothing is scraped until operator sets approved_by_operator = TRUE
  - DB write is handled by the orchestrator once Supabase is wired up
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
