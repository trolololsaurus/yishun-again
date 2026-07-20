"""
HWZ EDMW scraper — thread titles + stats ONLY (spec: signal, 60-minute interval).

LEGAL CONSTRAINT (never remove):
- source_type is always "signal" — EDMW is never a quotable source.
- content contains ONLY: thread title, reply count, view count.
- Post content is NEVER read, stored, or forwarded.
- EDMW signal_count is surfaced in War Room as "Forum buzz" only.
- Sources with type='signal' are never included in incident source_urls.

Parses the XenForo forum listing at the EDMW board URL directly.
The search endpoint (403) is not used.
"""

import json
import logging
import sys
import re
import time
from datetime import date

import httpx
from bs4 import BeautifulSoup

from . import BROWSER_HEADERS, content_matches_keywords, raise_scrape_failure

logger = logging.getLogger(__name__)

SOURCE_NAME = "HWZ EDMW"
SOURCE_TYPE = "signal"  # NEVER change — see legal constraint above

_FORUM_BASE = "https://forums.hardwarezone.com.sg"
_FORUM_URL  = "https://forums.hardwarezone.com.sg/forums/eat-drink-man-woman.16/"
_REQUEST_TIMEOUT = 15


def _parse_threads(html: str, debug: bool = False) -> list[dict]:
    """
    Parse thread rows from the EDMW forum listing page.

    Extracts ONLY: title, url, reply_count, view_count.
    Never reads post content.
    """
    soup = BeautifulSoup(html, "lxml")

    if debug:
        print("=== First 500 chars of parsed page text ===")
        print(soup.get_text()[:500])
        print()

    rows = soup.find_all("div", class_="structItem--thread")

    if debug:
        print(f"=== Thread rows found before filtering: {len(rows)} ===")
        for row in rows[:5]:
            title_tag = row.find("div", class_="structItem-title")
            title = title_tag.get_text(strip=True)[:80] if title_tag else "(no title)"
            meta = row.find("div", class_="structItem-cell--meta")
            meta_text = meta.get_text(separator=" | ", strip=True) if meta else "(no meta)"
            print(f"  Title: {title}")
            print(f"  Meta:  {meta_text}")
        print()

    threads = []
    for row in rows:
        # Title and URL
        title_div = row.find("div", class_="structItem-title")
        if not title_div:
            continue
        link = title_div.find("a", href=True)
        if not link:
            continue

        title = link.get_text(strip=True)
        href  = link["href"]
        url   = href if href.startswith("http") else f"{_FORUM_BASE}{href}"

        # Reply and view counts from the meta cell
        meta = row.find("div", class_="structItem-cell--meta")
        replies = "?"
        views   = "?"
        if meta:
            for dl in meta.find_all("dl"):
                dt = dl.find("dt")
                dd = dl.find("dd")
                if not dt or not dd:
                    continue
                label = dt.get_text(strip=True).lower()
                value = dd.get_text(strip=True)
                if label == "replies":
                    replies = value
                elif label == "views":
                    views = value

        # Thread START date, read from the LISTING only — XenForo renders it as
        # <li class="structItem-startDate"><time datetime="...ISO..."> in the row.
        # This is thread metadata, the same class of data as the reply/view
        # counts; the thread page is never fetched and post content is never
        # read. Without a date the candidate is dateless: it would bypass the
        # recency watermark and be re-processed by Stage 1/2 on every pass.
        started_at = None
        start_cell = row.find("li", class_="structItem-startDate")
        time_tag = start_cell.find("time") if start_cell else None
        stamp = time_tag.get("datetime") if time_tag else None
        if stamp:
            m = re.match(r"(\d{4})-(\d{2})-(\d{2})", stamp)
            if m:
                try:
                    started_at = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                except ValueError:
                    started_at = None

        threads.append({
            "title":      title,
            "url":        url,
            "replies":    replies,
            "views":      views,
            "started_at": started_at,
        })

    return threads


def scrape(debug: bool = False) -> list[dict]:
    """
    Return Yishun-relevant EDMW thread signals.

    content field contains ONLY thread title and stats — never post text.
    """
    results: list[dict] = []

    with httpx.Client(headers=BROWSER_HEADERS, follow_redirects=True) as client:
        try:
            resp = client.get(_FORUM_URL, timeout=_REQUEST_TIMEOUT)
            resp.raise_for_status()
        except Exception as exc:
            # Raise rather than returning [] — a blocked or moved forum must not
            # look like "no Yishun chatter". See scrapers.raise_scrape_failure.
            logger.error("EDMW fetch failed: %s", exc)
            raise_scrape_failure(SOURCE_NAME, exc)

        threads = _parse_threads(resp.text, debug=debug)
        logger.debug("EDMW: %d total threads before keyword filter", len(threads))

        seen_urls: set[str] = set()
        for thread in threads:
            if not content_matches_keywords(thread["title"]):
                continue
            if thread["url"] in seen_urls:
                continue
            seen_urls.add(thread["url"])

            content = (
                f"[EDMW signal] Thread: {thread['title']} | "
                f"Replies: {thread['replies']} | "
                f"Views: {thread['views']}"
            )
            results.append({
                "title":        thread["title"],
                "content":      content,
                "url":          thread["url"],
                "source_name":  SOURCE_NAME,
                "source_type":  SOURCE_TYPE,
                "published_at": thread.get("started_at"),
            })

    logger.info("EDMW: %d Yishun thread signal(s)", len(results))
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    items = scrape(debug=True)
    print(f"EDMW — {len(items)} Yishun signal(s) found after keyword filter.")
    if items:
        print("\nFirst result:")
        print(json.dumps(items[0], indent=2, ensure_ascii=False))
    else:
        print("No Yishun threads on current page — this is normal if none are trending.")
    sys.exit(0)
