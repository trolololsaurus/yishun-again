"""
Lianhe Zaobao scraper — HTML scrape, Chinese content (spec: msm, 180-minute interval).
Zaobao has no public RSS; we scrape their Singapore news section.
Pre-filter in Chinese before translation — no API call for non-matches.
translated_from: "zh" added to every output dict.
"""

import json
import logging
import sys
import time

import httpx
from bs4 import BeautifulSoup

from . import BROWSER_HEADERS, content_matches_lang, resolve_published_at, strip_html, translate_article

logger = logging.getLogger(__name__)

SOURCE_NAME    = "Lianhe Zaobao"
SOURCE_TYPE    = "msm"
_INDEX_URL     = "https://www.zaobao.com.sg/news/singapore"
_BASE_URL      = "https://www.zaobao.com.sg"
_LANG          = "zh"
_CONTENT_LIMIT = 3_000
_TIMEOUT       = 15
_DELAY         = 2

_HEADERS = {**BROWSER_HEADERS, "Accept-Language": "zh-SG,zh;q=0.9,en;q=0.8"}


def _fetch(client: httpx.Client, url: str) -> str | None:
    try:
        resp = client.get(url, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.text
    except Exception as exc:
        logger.warning("Zaobao fetch failed (%s): %s", url, exc)
        return None


def _extract_links(html: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html, "lxml")
    links: list[tuple[str, str]] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        text = a.get_text(strip=True)
        if not text or len(text) < 5:
            continue
        if not any(seg in href for seg in ("/realtime/", "/news/", "/singapore/", "/article/")):
            continue
        url = href if href.startswith("http") else f"{_BASE_URL}{href}"
        if url not in seen:
            seen.add(url)
            links.append((url, text))
    return links


def _fetch_body(client: httpx.Client, url: str) -> str:
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

    with httpx.Client(headers=_HEADERS, follow_redirects=True) as client:
        html = _fetch(client, _INDEX_URL)
        if not html:
            logger.warning("Zaobao: index fetch failed — skipping run")
            return results

        candidates = _extract_links(html)
        logger.debug("Zaobao: %d candidate links", len(candidates))

        for url, title_zh in candidates:
            if url in seen_urls:
                continue
            if not content_matches_lang(title_zh, _LANG):
                continue

            time.sleep(_DELAY)
            body_zh = _fetch_body(client, url) or title_zh

            try:
                en_title, en_content = translate_article(title_zh, body_zh, _LANG)
            except Exception as exc:
                logger.warning("Zaobao translation failed (%s): %s — skipping", url, exc)
                continue

            # Listing pages carry no date, so resolve it from the article itself.
            # A dateless candidate bypasses the recency watermark, is re-processed
            # by Stage 1/2 every pass, and cannot be approved until an operator
            # sets the date by hand (QA H3).
            published_at = resolve_published_at(url)

            seen_urls.add(url)
            results.append({
                "title":           en_title,
                "content":         en_content[:_CONTENT_LIMIT],
                "url":             url,
                "source_name":     SOURCE_NAME,
                "source_type":     SOURCE_TYPE,
                "translated_from": _LANG,
                "published_at":    published_at,
            })

    logger.info("Zaobao: %d Yishun items", len(results))
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    items = scrape()
    print(f"\nLianhe Zaobao -- {len(items)} Yishun item(s) found.")
    if items:
        print(json.dumps(items[0], indent=2, ensure_ascii=True))
    else:
        print("No Yishun items found (correct if no Yishun content today).")
    sys.exit(0)
