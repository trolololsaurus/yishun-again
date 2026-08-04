"""
Pluggable per-URL fetch strategies with a Wayback Machine fallback.

Why this exists: when a direct httpx GET of an ARTICLE URL is blocked or fails,
the story otherwise goes dateless-and-unapprovable (QA H3) — `resolve_published_at`
returns None, the candidate bypasses the recency watermark, is re-processed by
Stage 1/2 every pass, and can never be approved until an operator sets the date by
hand. Falling back to the newest Wayback snapshot recovers a date/body from the
archive instead of losing the story.

This is a PER-URL seam, deliberately distinct from `ingestion.fallback`'s
source-level FallbackLadder. That ladder decides "this whole source is blocked,
skip it and leave its watermark"; this decides "this one article URL failed the
direct fetch, try the archive before giving up". Different granularity, different
module — do not merge them.

No headless browser yet: `BrowserService` is a documented stub so a future
on-demand browser service slots into the chain without touching call sites.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx

from . import BROWSER_HEADERS

logger = logging.getLogger(__name__)

# Wayback can be slow, so every rung is bounded — a fetch failure must degrade to
# None, never an unbounded wait that stalls a whole pass.
_DIRECT_TIMEOUT        = 15.0
_WAYBACK_API_TIMEOUT   = 8.0
_WAYBACK_FETCH_TIMEOUT = 25.0
_WAYBACK_API           = "https://archive.org/wayback/available"
# Second endpoint, not a preference: /wayback/available rate-limits far more
# aggressively (429 on every call during the 2026-08-04 backfill) while CDX
# answered 200 for the same URLs in the same minute.
_WAYBACK_CDX           = "https://web.archive.org/cdx/search/cdx"


@dataclass
class FetchResult:
    """One successful fetch. `url` stays the canonical publisher URL (so it can be
    quoted as a clean source); `final_url` records where the fetch actually landed
    (post-redirect, or the archive.org snapshot URL when via='wayback')."""
    url: str
    final_url: str
    status: int
    html: str
    via: str  # 'direct' | 'wayback' | 'browser'


class FetchStrategy(ABC):
    """One rung of the fetch ladder. `fetch` returns a FetchResult on success or
    None on any failure — it must NEVER raise, so a rung can only ever be skipped,
    not crash the pass."""

    @abstractmethod
    def fetch(self, url: str, *, timeout: float | None = None) -> FetchResult | None:
        ...


class DirectHttpx(FetchStrategy):
    """Plain httpx GET with the browser UA the scrapers already use. This is the
    happy path — when it returns a result the caller sees identical behaviour to
    fetching directly, and Wayback never engages."""

    def fetch(self, url: str, *, timeout: float | None = None) -> FetchResult | None:
        if not url:
            return None
        timeout = _DIRECT_TIMEOUT if timeout is None else timeout
        try:
            resp = httpx.get(
                url, headers=BROWSER_HEADERS, follow_redirects=True, timeout=timeout,
            )
        except Exception as exc:  # transport error, timeout, bad TLS, …
            logger.debug("DirectHttpx: fetch failed for %s: %s", url[:80], exc)
            return None
        # A 403/429/5xx is exactly the "blocked" case we want to fall through to
        # Wayback on, so treat any non-200 or empty body as a miss, not a result.
        if resp.status_code != 200 or not resp.text:
            logger.debug("DirectHttpx: non-200/empty (%s) for %s", resp.status_code, url[:80])
            return None
        return FetchResult(
            url=url, final_url=str(resp.url), status=resp.status_code,
            html=resp.text, via="direct",
        )


class WaybackSnapshot(FetchStrategy):
    """Newest Wayback snapshot for the URL, so a blocked or dead article still
    yields a date/body instead of going dateless-and-unapprovable (QA H3)."""

    def fetch(self, url: str, *, timeout: float | None = None) -> FetchResult | None:
        if not url:
            return None
        snapshot = self._resolve_snapshot(url)
        if not snapshot:
            return None
        fetch_timeout = _WAYBACK_FETCH_TIMEOUT if timeout is None else timeout
        try:
            resp = httpx.get(
                snapshot, headers=BROWSER_HEADERS, follow_redirects=True,
                timeout=fetch_timeout,
            )
        except Exception as exc:
            logger.debug("WaybackSnapshot: snapshot fetch failed for %s: %s", snapshot[:80], exc)
            return None
        if resp.status_code != 200 or not resp.text:
            logger.debug("WaybackSnapshot: non-200/empty (%s) for %s", resp.status_code, snapshot[:80])
            return None
        # Keep `url` as the canonical publisher URL — source_urls must stay clean
        # (never an archive.org URL); `final_url` carries the snapshot we fetched.
        return FetchResult(
            url=url, final_url=snapshot, status=resp.status_code,
            html=resp.text, via="wayback",
        )

    @staticmethod
    def _resolve_snapshot(url: str) -> str | None:
        """Newest available snapshot URL for `url`, or None. Never raises.

        Reuses backfill_agent.get_wayback_url when importable (single source of
        truth for the availability API); the lazy import keeps that module's heavy
        deps (feedparser, dotenv) off both the module-load path and the happy path
        where direct succeeds and Wayback never runs. Falls back to a minimal
        inline query if the import fails, so this rung never depends on it."""
        try:
            from .backfill_agent import get_wayback_url
            return get_wayback_url(url)
        except Exception as exc:
            logger.debug("WaybackSnapshot: reuse import failed, using inline resolve: %s", exc)

        try:
            resp = httpx.get(
                _WAYBACK_API, params={"url": url}, headers=BROWSER_HEADERS,
                timeout=_WAYBACK_API_TIMEOUT,
            )
            resp.raise_for_status()
            closest = resp.json().get("archived_snapshots", {}).get("closest", {})
            if closest.get("available") and closest.get("url"):
                return closest["url"]
        except Exception as exc:
            logger.debug("WaybackSnapshot: availability API error for %s: %s", url[:80], exc)

        # CDX fallback. `/wayback/available` rate-limits hard — it answered 429
        # for every request during the 2026-08-04 date backfill, which silently
        # cost this whole rung. The CDX endpoint answered 200 for the same URLs
        # in the same minute, so it is tried second rather than not at all.
        try:
            resp = httpx.get(
                _WAYBACK_CDX,
                params={"url": url, "output": "json", "limit": -1,
                        "fl": "timestamp,original", "filter": "statuscode:200"},
                headers=BROWSER_HEADERS, timeout=_WAYBACK_API_TIMEOUT,
            )
            resp.raise_for_status()
            rows = resp.json()
            # First row is the header ["timestamp","original"].
            if isinstance(rows, list) and len(rows) > 1:
                timestamp, original = rows[-1][0], rows[-1][1]
                return f"https://web.archive.org/web/{timestamp}/{original}"
        except Exception as exc:
            logger.debug("WaybackSnapshot: CDX error for %s: %s", url[:80], exc)
        return None


class BrowserService(FetchStrategy):
    """STUB — the seam for a future on-demand headless-browser fetch.

    Some sources (JS-rendered listings, aggressive bot walls) will yield to a real
    browser when both direct and Wayback fail. That belongs in a SEPARATE
    on-demand Cloud Run service (Playwright is too heavy to carry in the agents
    image and must scale to zero), fronted by an HTTP call from here. Until it
    exists this rung is a no-op and is deliberately NOT in DEFAULT_CHAIN; wiring it
    in later needs no change to any call site — only this method and the chain."""

    def fetch(self, url: str, *, timeout: float | None = None) -> FetchResult | None:
        logger.debug("BrowserService: not implemented — skipping browser rung for %s", url[:80])
        return None


# Direct first (happy path), then the archive. BrowserService is intentionally
# omitted until the on-demand service exists.
DEFAULT_CHAIN: list[FetchStrategy] = [DirectHttpx(), WaybackSnapshot()]


def fetch_with_fallback(
    url: str,
    chain: list[FetchStrategy] = DEFAULT_CHAIN,
    *,
    timeout: float | None = None,
) -> FetchResult | None:
    """Try each strategy in order and return the first success, or None if all
    rungs miss. Never raises: a strategy that somehow throws is logged and skipped
    so a future half-built rung can't crash the pass it runs inside."""
    if not url:
        return None
    for strategy in chain:
        try:
            result = strategy.fetch(url, timeout=timeout)
        except Exception as exc:  # belt-and-suspenders — strategies shouldn't raise
            logger.debug("fetch_with_fallback: %s raised for %s: %s",
                         type(strategy).__name__, url[:80], exc)
            continue
        if result is not None:
            if result.via == "direct":
                logger.debug("fetch_with_fallback: %s via direct", url[:80])
            else:
                logger.info("fetch_with_fallback: recovered %s via %s", url[:80], result.via)
            return result
    logger.debug("fetch_with_fallback: all rungs failed for %s", url[:80])
    return None
