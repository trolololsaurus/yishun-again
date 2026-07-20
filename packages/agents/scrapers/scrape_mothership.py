"""
Mothership scraper — RSS-first (spec: msm, 60-minute interval).
"""

import json
import logging
import sys
import time
from datetime import date

import feedparser

from . import content_matches_keywords, strip_html

logger = logging.getLogger(__name__)

SOURCE_NAME = "Mothership"
SOURCE_TYPE = "msm"

# Standard WordPress RSS feed.
_RSS_URL = "https://mothership.sg/feed/"

_CONTENT_LIMIT = 3_000


def scrape() -> list[dict]:
    """Return Yishun-relevant Mothership articles from the RSS feed."""
    results: list[dict] = []
    seen_urls: set[str] = set()

    try:
        feed = feedparser.parse(_RSS_URL)

        if feed.bozo and not feed.entries:
            logger.warning("Mothership RSS parse error: %s", feed.bozo_exception)
            return results

        for entry in feed.entries:
            url = entry.get("link", "").strip()
            if not url or url in seen_urls:
                continue

            title = entry.get("title", "").strip()
            summary = entry.get("summary", "") or entry.get("description", "")

            content_nodes = entry.get("content", [])
            raw_content = content_nodes[0].get("value", summary) if content_nodes else summary
            content = strip_html(raw_content)

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
        logger.error("Mothership scraper error: %s", exc)

    logger.info("Mothership: %d Yishun items", len(results))
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    items = scrape()
    print(f"\nMothership — {len(items)} Yishun item(s) found.")
    if items:
        print("First result:")
        print(json.dumps(items[0], indent=2, ensure_ascii=False))
    else:
        print("No Yishun items found in this run.")
    sys.exit(0)
