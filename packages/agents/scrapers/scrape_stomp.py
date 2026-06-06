"""
Stomp scraper — HTML scrape (spec: msm, 120-minute interval).

Stomp has no public RSS feed. We search their site for 'yishun' and scrape
the article listing. Full article content is fetched on a per-article basis
with polite delays. Fails gracefully — a single article error never stops
the rest of the run.
"""

import json
import logging
import sys
import time

import httpx
from bs4 import BeautifulSoup

from . import BROWSER_HEADERS, content_matches_keywords, strip_html

logger = logging.getLogger(__name__)

SOURCE_NAME = "Stomp"
SOURCE_TYPE = "msm"

_BASE_URL   = "https://stomp.straitstimes.com"
_SEARCH_URL = "https://stomp.straitstimes.com/search"
_CONTENT_LIMIT  = 3_000
_REQUEST_TIMEOUT = 15
_INTER_REQUEST_DELAY = 2  # seconds between article fetches


def _fetch(client: httpx.Client, url: str) -> httpx.Response | None:
    try:
        resp = client.get(url, timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp
    except Exception as exc:
        logger.warning("Stomp fetch failed (%s): %s", url, exc)
        return None


def _parse_listing(html: str) -> list[dict]:
    """Extract article {title, url} pairs from a Stomp search results page."""
    soup = BeautifulSoup(html, "lxml")
    items = []

    # Stomp search results render articles as <article> or within link cards.
    # We cast a wide net — any <a> with a Stomp path that looks like an article.
    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        # Stomp article URLs follow the pattern /singapore-seen/<topic>/<id>
        if href.startswith("/") and href.count("/") >= 2 and not href.endswith("/search"):
            url = _BASE_URL + href if href.startswith("/") else href
            # Get the closest text that looks like a title
            title = tag.get_text(strip=True)
            if title and len(title) > 15:
                items.append({"title": title, "url": url})

    # Deduplicate by URL
    seen: set[str] = set()
    unique = []
    for item in items:
        if item["url"] not in seen:
            seen.add(item["url"])
            unique.append(item)
    return unique


def _fetch_article_content(client: httpx.Client, url: str) -> str:
    """Fetch a single Stomp article and extract its body text."""
    resp = _fetch(client, url)
    if not resp:
        return ""
    soup = BeautifulSoup(resp.text, "lxml")
    # Article body is typically inside <div class="article-body"> or similar
    body = (
        soup.find("div", class_="article-body")
        or soup.find("div", class_="article-content")
        or soup.find("article")
    )
    if body:
        return body.get_text(separator=" ", strip=True)[:_CONTENT_LIMIT]
    # Fall back: extract all paragraph text
    paras = soup.find_all("p")
    return " ".join(p.get_text(strip=True) for p in paras)[:_CONTENT_LIMIT]


def scrape() -> list[dict]:
    """Return Yishun-relevant Stomp articles."""
    results: list[dict] = []

    with httpx.Client(headers=BROWSER_HEADERS, follow_redirects=True) as client:
        # Search Stomp for 'yishun'
        resp = _fetch(client, f"{_SEARCH_URL}?q=yishun")
        if not resp:
            logger.warning("Stomp: search request failed — skipping run")
            return results

        candidates = _parse_listing(resp.text)
        logger.debug("Stomp: %d candidate links from search", len(candidates))

        for item in candidates:
            title = item["title"]
            url   = item["url"]

            # Quick title check before fetching the full article
            if not content_matches_keywords(title):
                continue

            time.sleep(_INTER_REQUEST_DELAY)
            content = _fetch_article_content(client, url)
            if not content:
                content = title  # use title as fallback so Stage 1 can still judge

            results.append({
                "title":       title,
                "content":     content,
                "url":         url,
                "source_name": SOURCE_NAME,
                "source_type": SOURCE_TYPE,
            })

    logger.info("Stomp: %d Yishun items", len(results))
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    items = scrape()
    print(f"\nStomp — {len(items)} Yishun item(s) found.")
    if items:
        print("First result:")
        print(json.dumps(items[0], indent=2, ensure_ascii=False))
    else:
        print("No Yishun items found in this run.")
    sys.exit(0)
