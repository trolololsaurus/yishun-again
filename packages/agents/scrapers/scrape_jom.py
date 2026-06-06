"""
Jom scraper — RSS feed via httpx (spec: msm, 360-minute interval).
Jom's SSL cert causes feedparser's urllib to abort; we fetch with httpx
(which handles it correctly) then hand the raw XML to feedparser.
"""

import json
import logging
import sys

import feedparser
import httpx

from . import content_matches_keywords, strip_html

logger = logging.getLogger(__name__)

SOURCE_NAME    = "Jom"
SOURCE_TYPE    = "msm"
_RSS_URL       = "https://jom.media/feed/"
_BROWSER_UA    = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
_CONTENT_LIMIT = 3_000
_TIMEOUT       = 20


def scrape() -> list[dict]:
    results:   list[dict] = []
    seen_urls: set[str]   = set()

    try:
        # httpx handles Jom's SSL correctly; pass raw XML text to feedparser
        resp = httpx.get(
            _RSS_URL,
            headers={"User-Agent": _BROWSER_UA},
            timeout=_TIMEOUT,
            follow_redirects=True,
            verify=False,   # jom.media SSL cert causes handshake issues with system trust store
        )
        resp.raise_for_status()
        feed = feedparser.parse(resp.text)

        if feed.bozo and not feed.entries:
            logger.warning("Jom RSS parse error: %s", feed.bozo_exception)
            return results

        for entry in feed.entries:
            url   = entry.get("link", "").strip()
            title = entry.get("title", "").strip()
            if not url or url in seen_urls:
                continue

            summary      = entry.get("summary", "") or entry.get("description", "")
            content_list = entry.get("content", [])
            raw          = content_list[0].get("value", summary) if content_list else summary
            content      = strip_html(raw)

            if not content_matches_keywords(f"{title} {content}"):
                continue

            seen_urls.add(url)
            results.append({
                "title":       title,
                "content":     content[:_CONTENT_LIMIT],
                "url":         url,
                "source_name": SOURCE_NAME,
                "source_type": SOURCE_TYPE,
            })

    except Exception as exc:
        logger.error("Jom scraper error: %s", exc)

    logger.info("Jom: %d Yishun items", len(results))
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    items = scrape()
    print(f"\nJom — {len(items)} Yishun item(s) found.")
    if items:
        print(json.dumps(items[0], indent=2, ensure_ascii=False))
    sys.exit(0)
