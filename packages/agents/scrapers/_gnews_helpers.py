"""
Shared Google News RSS helpers (INGESTION_DESIGN.md §10b step 10).

Extracted from scrapers/backfill_agent.py so both the deprecation-guarded
backfill agent and ingestion/sources/google_news_rss.py can import them
without pulling in backfill_agent's module-level state.
"""

import httpx

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def _gnews_source_name(entry) -> str:
    """Extract outlet name from a Google News RSS entry."""
    source = getattr(entry, "source", None)
    if source and hasattr(source, "title"):
        return str(source.title)
    # Fallback: Google News RSS appends " - Source Name" to the article title
    title = entry.get("title", "")
    if " - " in title:
        return title.rsplit(" - ", 1)[-1].strip()
    return "Google News"


def _resolve_redirect(raw_url: str) -> str:
    """
    Follow a Google News redirect URL to get the real article URL.
    Times out quickly; returns raw_url on failure.
    """
    try:
        resp = httpx.get(
            raw_url,
            follow_redirects=True,
            timeout=8.0,
            headers={"User-Agent": _BROWSER_UA},
        )
        final = str(resp.url)
        # Reject if we ended up back on news.google.com (redirect failed)
        return raw_url if "news.google.com" in final else final
    except Exception:
        return raw_url
