"""
CNA scraper — RSS via API feed (spec: msm, 60-minute interval).

CNA's /rss/* paths are 404. The live feed endpoint is:
  /api/v1/rss-outbound-feed?_format=xml&category=<id>
  10416 = Singapore local news (primary)
  6511  = Latest news (catch-all, broader)
"""

import json
import logging
import sys
import time
from datetime import date

import feedparser

from . import content_matches_keywords, strip_html

logger = logging.getLogger(__name__)

SOURCE_NAME = "CNA"
SOURCE_TYPE = "msm"

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_RSS_URLS = [
    "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml&category=10416",
    "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml&category=6511",
]

_CONTENT_LIMIT = 3_000


def scrape() -> list[dict]:
    """Return Yishun-relevant CNA articles from RSS feeds."""
    results: list[dict] = []
    seen_urls: set[str] = set()

    feedparser.USER_AGENT = _BROWSER_UA

    for feed_url in _RSS_URLS:
        try:
            feed = feedparser.parse(feed_url)

            if feed.bozo and not feed.entries:
                logger.warning("CNA RSS parse error (%s): %s", feed_url, feed.bozo_exception)
                continue

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
            logger.error("CNA scraper error (%s): %s", feed_url, exc)

        time.sleep(1)

    logger.info("CNA: %d Yishun items", len(results))
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    items = scrape()
    print(f"\nCNA — {len(items)} Yishun item(s) found.")
    if items:
        print("First result:")
        print(json.dumps(items[0], indent=2, ensure_ascii=False))
    else:
        print("No Yishun items found in this run.")
    sys.exit(0)
