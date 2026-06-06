"""
Tamil Murasu scraper — HTML scrape, Tamil content (spec: msm, 180-minute interval).

Tamil Murasu has no confirmed public RSS. We scrape the main page for article links,
pre-filter for Yishun in Tamil/English, then translate matches with Claude Haiku.
translated_from: "ta" is added to every output dict.
"""

import json
import logging
import sys
import time

import httpx
from bs4 import BeautifulSoup

from . import BROWSER_HEADERS, content_matches_lang, strip_html, translate_article

logger = logging.getLogger(__name__)

SOURCE_NAME    = "Tamil Murasu"
SOURCE_TYPE    = "msm"
_BASE_URL      = "https://tamilmurasu.com.sg"
_INDEX_URL     = "https://tamilmurasu.com.sg"
_LANG          = "ta"
_CONTENT_LIMIT = 3_000
_TIMEOUT       = 15
_DELAY         = 2


def _fetch(client: httpx.Client, url: str) -> str | None:
    try:
        resp = client.get(url, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.text
    except Exception as exc:
        logger.warning("Tamil Murasu fetch failed (%s): %s", url, exc)
        return None


def _extract_article_links(html: str) -> list[tuple[str, str]]:
    """Return [(url, title_text)] from the main page listing."""
    soup = BeautifulSoup(html, "lxml")
    links = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        text = a.get_text(strip=True)
        if not text or len(text) < 5:
            continue
        # Tamil Murasu article URLs typically contain /news/ or a date pattern
        if not any(seg in href for seg in ("/news/", "/article/", "/stories/")):
            continue
        url = href if href.startswith("http") else f"{_BASE_URL}{href}"
        if url not in seen:
            seen.add(url)
            links.append((url, text))
    return links


def _fetch_article_body(client: httpx.Client, url: str) -> str:
    html = _fetch(client, url)
    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    body = (
        soup.find("div", class_=lambda c: c and "article" in c.lower())
        or soup.find("article")
        or soup.find("main")
    )
    if body:
        return body.get_text(separator=" ", strip=True)[:_CONTENT_LIMIT]
    return " ".join(p.get_text(strip=True) for p in soup.find_all("p"))[:_CONTENT_LIMIT]


def scrape() -> list[dict]:
    results:   list[dict] = []
    seen_urls: set[str]   = set()

    with httpx.Client(headers=BROWSER_HEADERS, follow_redirects=True) as client:
        html = _fetch(client, _INDEX_URL)
        if not html:
            logger.warning("Tamil Murasu: main page fetch failed — skipping run")
            return results

        candidates = _extract_article_links(html)
        logger.debug("Tamil Murasu: %d candidate links", len(candidates))

        for url, title_ta in candidates:
            if url in seen_urls:
                continue

            # Pre-filter: Tamil Murasu often writes "Yishun" in Latin script too
            if not content_matches_lang(title_ta, _LANG):
                continue

            time.sleep(_DELAY)
            body_ta = _fetch_article_body(client, url) or title_ta

            try:
                en_title, en_content = translate_article(title_ta, body_ta, _LANG)
            except Exception as exc:
                logger.warning("Tamil Murasu translation failed (%s): %s — skipping", url, exc)
                continue

            seen_urls.add(url)
            results.append({
                "title":           en_title,
                "content":         en_content[:_CONTENT_LIMIT],
                "url":             url,
                "source_name":     SOURCE_NAME,
                "source_type":     SOURCE_TYPE,
                "translated_from": _LANG,
            })

    logger.info("Tamil Murasu: %d Yishun items", len(results))
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    items = scrape()
    print(f"\nTamil Murasu — {len(items)} Yishun item(s) found.")
    if items:
        print(json.dumps(items[0], indent=2, ensure_ascii=False))
    else:
        print("No Yishun items found (correct if no Yishun content today).")
    sys.exit(0)
