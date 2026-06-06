"""
Reddit scraper — RSS via feedparser (spec: reddit, 30-minute interval).

Uses the Reddit RSS search endpoint directly. feedparser is not blocked the
way the JSON API is. lxml-xml is used as the parser for RSS feeds.
No auth or credentials required.
"""

import json
import logging
import sys
import time

import feedparser

from . import content_matches_keywords, strip_html

logger = logging.getLogger(__name__)

SOURCE_TYPE    = "reddit"
_CONTENT_LIMIT = 3_000

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_SUBREDDITS = [
    {
        "name":    "Reddit Singapore",
        "rss_url": (
            "https://www.reddit.com/r/singapore/search.rss"
            "?q=yishun&sort=new&restrict_sr=on"
        ),
    },
    {
        "name":    "Reddit SingaporeRaw",
        "rss_url": (
            "https://www.reddit.com/r/singaporeraw/search.rss"
            "?q=yishun&sort=new&restrict_sr=on"
        ),
    },
]


def _parse_feed(rss_url: str, source_name: str) -> list[dict]:
    """Fetch and parse one subreddit RSS feed, returning Yishun-relevant posts."""
    # Set feedparser's global UA before each call so it's never the default bot string.
    feedparser.USER_AGENT = _BROWSER_UA

    feed = feedparser.parse(rss_url)

    if feed.bozo and not feed.entries:
        logger.warning(
            "Reddit RSS parse error [%s]: %s", source_name, feed.bozo_exception
        )
        return []

    results = []
    for entry in feed.entries:
        url   = entry.get("link", "").strip()
        title = entry.get("title", "").strip()

        # RSS summary contains the post body (HTML-encoded) for text posts,
        # or just the title snippet for link posts.
        raw_summary = entry.get("summary", "") or ""
        summary     = strip_html(raw_summary)

        content = f"{title}\n\n{summary}".strip() if summary else title

        if not url or not content_matches_keywords(content):
            continue

        results.append({
            "title":       title,
            "content":     content[:_CONTENT_LIMIT],
            "url":         url,
            "source_name": source_name,
            "source_type": SOURCE_TYPE,
        })

    logger.info("Reddit [%s]: %d Yishun posts", source_name, len(results))
    return results


def scrape() -> list[dict]:
    """Return Yishun-relevant posts from r/singapore and r/singaporeraw."""
    results:   list[dict] = []
    seen_urls: set[str]   = set()

    for sub in _SUBREDDITS:
        posts = _parse_feed(sub["rss_url"], sub["name"])
        for post in posts:
            if post["url"] not in seen_urls:
                seen_urls.add(post["url"])
                results.append(post)
        time.sleep(2)

    logger.info("Reddit total: %d Yishun items", len(results))
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    items = scrape()
    print(f"\nReddit — {len(items)} Yishun item(s) found.")
    if items:
        print("First result:")
        print(json.dumps(items[0], indent=2, ensure_ascii=False))
    else:
        print("No Yishun items found in this run.")
    sys.exit(0)
