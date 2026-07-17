"""
Historical search agent — replacement for the broken Google News RSS
date-range backfill.

Google News RSS (`news.google.com/rss/search`) does not honour `after:`/
`before:` query operators (they return zero results) and only indexes a
recent rolling window (~1-2 years) regardless of query. It cannot be used
for pre-2024 historical backfill.

This agent searches sources that actually have historical archives:

  1. Google News web search (`tbm=nws`) — full historical archive via the
                                       human search UI; handled defensively
                                       (CAPTCHA-aware).
  2. CNA via Google `site:` search    — same mechanism, scoped to
                                       channelnewsasia.com for higher-quality
                                       MSM results.

GDELT (429 on every call) and Yahoo News SG search (404 on every call) were
tried and found broken — removed.

Candidates are produced in the standard backfill format
({title, content, url, source_name, source_type, date}) and fed into the
existing Stage 1 -> Stage 2 -> dedup -> consolidation -> tier pipeline via
`process_candidates()` from backfill_agent.py.

DRY RUN:
    python -m scrapers.backfill_agent --historical-search \\
        --start-year=2020 --end-year=2021 --no-wikipedia --dry-run --limit=30

LIVE RUN:
    python -m scrapers.backfill_agent --historical-search \\
        --start-year=2015 --end-year=2023 --limit=500
"""

import logging
import random
import re
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from scrapers import BROWSER_HEADERS, content_matches_keywords
from filters.stage1_quota import Stage1DailyQuota

# Explicit path so the module finds .env regardless of CWD
_repo_root = Path(__file__).resolve().parents[3]
load_dotenv(_repo_root / ".env", override=False)

logger = logging.getLogger(__name__)


# ── Keywords ──────────────────────────────────────────────────────────────────
# Covers all classification types, not just crime — heart/clown/dagger.

HISTORICAL_KEYWORDS = [
    # DAGGER — crime and dark events
    "murder", "stabbing", "killed", "assault", "crime",
    "arrested", "sentenced", "robbery", "scam", "accident",
    # CLOWN — absurdities
    "viral", "weird", "bizarre", "unusual", "funny",
    # HEART — community and development
    "new", "opened", "development", "school", "hospital",
    "mrt", "community", "park", "library", "built",
    # Broad sweep
    "incident", "yishun",
]


# ── Rate limits ───────────────────────────────────────────────────────────────

GOOGLE_WEB_RATE_MIN   = 3.0
GOOGLE_WEB_RATE_MAX   = 5.0


# ── SOURCE 1 — Google News web search (tbm=nws) ──────────────────────────────

_DATE_PATTERN = re.compile(
    r"\b(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{4})\b",
    re.IGNORECASE,
)
_BLOCKED_MARKERS = (
    "unusual traffic",
    "/sorry/",
    "did not match any documents",
    "captcha",
)


def _parse_news_date(text: str) -> Optional[str]:
    text = text.strip().rstrip(".")
    for fmt in ("%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _search_google_web(keyword: str, year: int, seen_urls: set) -> list:
    """
    Scrape google.com/search?tbm=nws with a year date-range filter
    (cdr:1,cd_min/cd_max). Defensive: any non-200, CAPTCHA, or "no results"
    page is logged and skipped — never raises.
    """
    query = f"yishun {keyword}"
    url = (
        "https://www.google.com/search"
        f"?q={urllib.parse.quote(query)}"
        f"&tbm=nws"
        f"&tbs=cdr:1,cd_min:{year}-01-01,cd_max:{year}-12-31"
        f"&hl=en&gl=SG&num=20"
    )

    try:
        resp = httpx.get(url, headers=BROWSER_HEADERS, timeout=15.0, follow_redirects=True)
    except Exception as exc:
        logger.warning("Google web search request error [%s %d]: %s", keyword, year, exc)
        return []

    if resp.status_code != 200:
        logger.warning("Google web search non-200 [%s %d]: %d", keyword, year, resp.status_code)
        return []

    text_lower = resp.text.lower()
    if any(marker in text_lower for marker in _BLOCKED_MARKERS):
        logger.warning("Google web search blocked/empty [%s %d] — skipping", keyword, year)
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    items = []

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/url?q=" not in href:
            continue

        real_url = urllib.parse.unquote(href.split("/url?q=")[1].split("&")[0])
        if not real_url.startswith("http") or real_url in seen_urls:
            continue

        title = a.get_text(" ", strip=True)
        if not title or not content_matches_keywords(title):
            continue

        # Best-effort date: scan ancestor text for a "D Mon YYYY" pattern.
        # If no exact date is found, fall back to year-precision (YYYY-01-01),
        # matching Wikipedia's year-precision convention — an honest
        # approximation rather than a fabricated mid-year date.
        date = f"{year}-01-01"
        node = a.parent
        for _ in range(3):
            if node is None:
                break
            m = _DATE_PATTERN.search(node.get_text(" ", strip=True))
            if m:
                parsed = _parse_news_date(m.group(1))
                if parsed:
                    date = parsed
                break
            node = node.parent

        seen_urls.add(real_url)
        items.append({
            "title":       title,
            "content":     title,
            "url":         real_url,
            "source_name": urllib.parse.urlparse(real_url).netloc,
            "source_type": "msm",
            "date":        date,
        })

    logger.debug("Google web [%s %d]: %d new items", keyword, year, len(items))
    return items


# ── SOURCE 2 — CNA via Google site search ────────────────────────────────────

def _search_cna_google(keyword: str, year: int, seen_urls: set) -> list:
    """
    Search CNA's historical archive via Google's `site:` operator, scoped to
    channelnewsasia.com and the same year date-range filter as
    `_search_google_web`. Same defensive handling and HTML parsing.
    """
    query = f"site:channelnewsasia.com yishun {keyword}"
    url = (
        "https://www.google.com/search"
        f"?q={urllib.parse.quote(query)}"
        f"&tbs=cdr:1,cd_min:{year}-01-01,cd_max:{year}-12-31"
        f"&hl=en&gl=SG&num=10"
    )

    try:
        resp = httpx.get(url, headers=BROWSER_HEADERS, timeout=15.0, follow_redirects=True)
    except Exception as exc:
        logger.warning("CNA Google search request error [%s %d]: %s", keyword, year, exc)
        return []

    if resp.status_code != 200:
        logger.warning("CNA Google search non-200 [%s %d]: %d", keyword, year, resp.status_code)
        return []

    text_lower = resp.text.lower()
    if any(marker in text_lower for marker in _BLOCKED_MARKERS):
        logger.warning("CNA Google search blocked/empty [%s %d] — skipping", keyword, year)
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    items = []

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/url?q=" not in href:
            continue

        real_url = urllib.parse.unquote(href.split("/url?q=")[1].split("&")[0])
        if not real_url.startswith("http") or real_url in seen_urls:
            continue

        title = a.get_text(" ", strip=True)
        if not title or not content_matches_keywords(title):
            continue

        # Best-effort date: scan ancestor text for a "D Mon YYYY" pattern.
        # Fall back to year-precision (YYYY-01-01) if not found.
        date = f"{year}-01-01"
        node = a.parent
        for _ in range(3):
            if node is None:
                break
            m = _DATE_PATTERN.search(node.get_text(" ", strip=True))
            if m:
                parsed = _parse_news_date(m.group(1))
                if parsed:
                    date = parsed
                break
            node = node.parent

        seen_urls.add(real_url)
        items.append({
            "title":       title,
            "content":     title,
            "url":         real_url,
            "source_name": "Channel NewsAsia",
            "source_type": "msm",
            "date":        date,
        })

    logger.debug("CNA Google [%s %d]: %d new items", keyword, year, len(items))
    return items


# ── Combined search ───────────────────────────────────────────────────────────

def search_historical(
    keyword: str,
    year: int,
    seen_urls: set,
    sources: list = ["google", "cna_google"],
) -> list:
    """
    Search all configured sources for keyword+year.
    Returns list of candidate dicts in standard backfill format:
      {title, content, url, source_name, source_type, date}
    Deduplicates by URL using seen_urls (shared across sources/keywords/years).
    """
    items: list = []

    if "google" in sources:
        try:
            items.extend(_search_google_web(keyword, year, seen_urls))
        except Exception as exc:
            logger.warning("Google web search unexpected error [%s %d]: %s", keyword, year, exc)
        time.sleep(random.uniform(GOOGLE_WEB_RATE_MIN, GOOGLE_WEB_RATE_MAX))

    if "cna_google" in sources:
        try:
            items.extend(_search_cna_google(keyword, year, seen_urls))
        except Exception as exc:
            logger.warning("CNA Google search unexpected error [%s %d]: %s", keyword, year, exc)
        time.sleep(random.uniform(GOOGLE_WEB_RATE_MIN, GOOGLE_WEB_RATE_MAX))

    return items


# ── Main entry point ──────────────────────────────────────────────────────────

def run_historical_search(
    start_year: int = 2015,
    end_year: int = 2023,
    include_wikipedia: bool = False,
    dry_run: bool = False,
    limit: int = 500,
    sources: list = ["google", "cna_google"],
) -> dict:
    """
    Main entry point. Loops year x keyword x source to collect candidates,
    then feeds them into the same Stage 1 -> Stage 2 -> dedup ->
    consolidation -> tier pipeline used by backfill_agent.run_backfill()
    (via process_candidates).

    Returns a stats dict with the same shape as run_backfill()'s.
    """
    from scrapers.backfill_agent import (
        process_candidates,
        _scrape_wikipedia,
        _write_summary_notification,
        WIKI_YEAR_MIN,
        WIKI_YEAR_MAX,
    )
    from classifiers.corroboration import get_supabase_client

    end_year = min(end_year, 2026)
    budget   = Stage1DailyQuota()
    run_at   = datetime.now(timezone.utc).isoformat()

    stats: dict = {
        "run_at":             run_at,
        "dry_run":            dry_run,
        "scraped":            0,
        "stage1_passed":      0,
        "stage1_rejected":    0,
        "stage2_processed":   0,
        "stage2_errors":      0,
        "duplicates_skipped": 0,
        "prefiltered":        0,
        "batch_merges":       0,
        "auto_published":     0,
        "queued_for_review":  0,
        "updates_found":      0,
        "rejected":           0,
        "errors":             0,
        "capped":             False,
        "items":              [],
        "error_details":      [],
    }

    supabase = None
    if not dry_run:
        try:
            supabase = get_supabase_client()
        except EnvironmentError as exc:
            logger.error("Supabase not configured — cannot run live historical search: %s", exc)
            stats["errors"] += 1
            return stats

    cap = max(1, int(limit))

    # ── Phase 1: Collect all raw candidates ──────────────────────────────────

    seen_urls: set = set()
    all_candidates: list = []

    logger.info(
        "Historical search — collecting candidates: %d-%d, %d keywords, sources=%s",
        start_year, end_year, len(HISTORICAL_KEYWORDS), "+".join(sources),
    )

    for year in range(start_year, end_year + 1):
        for keyword in HISTORICAL_KEYWORDS:
            items = search_historical(keyword, year, seen_urls, sources=sources)
            all_candidates.extend(items)
            stats["scraped"] += len(items)

            if stats["scraped"] >= cap:
                stats["capped"] = True
                logger.info(
                    "Historical search: raw cap (%d) reached at year=%d keyword=%s",
                    cap, year, keyword,
                )
                break

        if stats["capped"]:
            break

    if include_wikipedia and not stats["capped"]:
        logger.info("Historical search — collecting Wikipedia candidates (1980–2014)")
        wiki_items = _scrape_wikipedia(seen_urls)
        all_candidates.extend(wiki_items)
        stats["scraped"] += len(wiki_items)

    logger.info(
        "Historical search — %d raw candidates collected (capped=%s)",
        len(all_candidates), stats["capped"],
    )

    # ── Phase 2: Stage 1 -> Stage 2 -> dedup -> consolidation -> tier routing ─

    process_candidates(all_candidates, cap, dry_run, supabase, stats, budget)

    # ── Write War Room summary notification ───────────────────────────────────
    if not dry_run and supabase is not None:
        _write_summary_notification(stats, supabase)

    logger.info(
        "Historical search done: scraped=%d s1_pass=%d s2_ok=%d dupes=%d "
        "auto_pub=%d queued=%d updates=%d rejected=%d errors=%d",
        stats["scraped"], stats["stage1_passed"], stats["stage2_processed"],
        stats["duplicates_skipped"], stats["auto_published"],
        stats["queued_for_review"], stats["updates_found"],
        stats["rejected"], stats["errors"],
    )

    year_range = f"{start_year}-{end_year} (historical-search)"
    if include_wikipedia:
        year_range += f" + wikipedia {WIKI_YEAR_MIN}-{WIKI_YEAR_MAX}"

    budget.write_log(extra={"year_range": year_range, "limit": limit})

    return stats
