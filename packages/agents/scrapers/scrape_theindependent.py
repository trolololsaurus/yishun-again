"""
The Independent Singapore scraper — RSS feed (spec: msm, 60-minute interval).
"""

import json
import logging
import sys
from datetime import date

import feedparser

from . import content_matches_keywords, strip_html

logger = logging.getLogger(__name__)

SOURCE_NAME = "The Independent Singapore"
SOURCE_TYPE = "msm"
_RSS_URL    = "https://theindependent.sg/feed/"
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
_CONTENT_LIMIT = 3_000


def scrape() -> list[dict]:
    results:   list[dict] = []
    seen_urls: set[str]   = set()

    feedparser.USER_AGENT = _BROWSER_UA

    try:
        feed = feedparser.parse(_RSS_URL)
        if feed.bozo and not feed.entries:
            logger.warning("The Independent RSS parse error: %s", feed.bozo_exception)
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

            # Without a date the candidate is "dateless": it bypasses the recency
            # watermark, is re-processed by Stage 1/2 every pass, and the operator
            # must set the date by hand before approval (QA H3). The feed carries
            # pubDate on every entry, so capture it. Same shape as scrape_cna.
            published_at = None
            pp = entry.get("published_parsed")
            if pp:
                try:
                    published_at = date(*pp[:3])
                except (TypeError, ValueError):
                    pass

            seen_urls.add(url)
            results.append({
                "title":        title,
                "content":      content[:_CONTENT_LIMIT],
                "url":          url,
                "source_name":  SOURCE_NAME,
                "source_type":  SOURCE_TYPE,
                "published_at": published_at,
            })

    except Exception as exc:
        logger.error("The Independent scraper error: %s", exc)

    logger.info("The Independent: %d Yishun items", len(results))
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    items = scrape()
    print(f"\nThe Independent — {len(items)} Yishun item(s) found.")
    if items:
        print(json.dumps(items[0], indent=2, ensure_ascii=False))
    sys.exit(0)
