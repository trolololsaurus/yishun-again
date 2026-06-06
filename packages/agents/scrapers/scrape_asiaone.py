"""
AsiaOne scraper — HTML scrape (spec: msm, 120-minute interval).
AsiaOne removed their public RSS feed; we scrape their Singapore news listing.
"""

import json
import logging
import sys
import time

import httpx
from bs4 import BeautifulSoup

from . import BROWSER_HEADERS, content_matches_keywords, strip_html

logger = logging.getLogger(__name__)

SOURCE_NAME    = "AsiaOne"
SOURCE_TYPE    = "msm"
_SEARCH_URL    = "https://www.asiaone.com/singapore"
_BASE_URL      = "https://www.asiaone.com"
_CONTENT_LIMIT = 3_000
_TIMEOUT       = 15
_DELAY         = 2


def _fetch(client: httpx.Client, url: str) -> str | None:
    try:
        resp = client.get(url, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.text
    except Exception as exc:
        logger.warning("AsiaOne fetch failed (%s): %s", url, exc)
        return None


def _parse_listing(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    items = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href  = a["href"].strip()
        title = a.get_text(strip=True)
        if not title or len(title) < 15:
            continue
        if not any(seg in href for seg in ("/singapore/", "/news/", "/article/")):
            continue
        url = href if href.startswith("http") else f"{_BASE_URL}{href}"
        if url not in seen:
            seen.add(url)
            items.append({"url": url, "title": title})
    return items


def _fetch_body(client: httpx.Client, url: str) -> str:
    html = _fetch(client, url)
    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    body = (
        soup.find("div", class_=lambda c: c and "article" in c.lower())
        or soup.find("article")
    )
    if body:
        return body.get_text(separator=" ", strip=True)[:_CONTENT_LIMIT]
    return " ".join(p.get_text(strip=True) for p in soup.find_all("p"))[:_CONTENT_LIMIT]


def scrape() -> list[dict]:
    results:   list[dict] = []
    seen_urls: set[str]   = set()

    with httpx.Client(headers=BROWSER_HEADERS, follow_redirects=True) as client:
        html = _fetch(client, _SEARCH_URL)
        if not html:
            logger.warning("AsiaOne: listing fetch failed — skipping run")
            return results

        candidates = _parse_listing(html)
        logger.debug("AsiaOne: %d candidate links", len(candidates))

        for item in candidates:
            url, title = item["url"], item["title"]
            if url in seen_urls:
                continue
            if not content_matches_keywords(title):
                continue

            time.sleep(_DELAY)
            content = _fetch_body(client, url) or title

            seen_urls.add(url)
            results.append({
                "title":       title,
                "content":     content,
                "url":         url,
                "source_name": SOURCE_NAME,
                "source_type": SOURCE_TYPE,
            })

    logger.info("AsiaOne: %d Yishun items", len(results))
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    items = scrape()
    print(f"\nAsiaOne — {len(items)} Yishun item(s) found.")
    if items:
        print(json.dumps(items[0], indent=2, ensure_ascii=False))
    sys.exit(0)
