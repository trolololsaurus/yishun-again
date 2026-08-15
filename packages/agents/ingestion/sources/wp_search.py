"""
WordPress search-feed source adapter — DISCOVERY role.

Part of the 2026-08-02 replacement of `google_news_rss` (see news_sitemap.py
for the full rationale). Where that module covers publishers who ship a
Google-News sitemap, this one covers the WordPress shops that do not.

WordPress answers `?s=<term>&feed=rss2` with a real RSS feed of SEARCH RESULTS
— dated entries, full canonical permalinks, article summaries in the body. That
is a keyword-scoped archive query against the publisher's own site: exactly
what Google News was being used for, minus the redirect wrapper and minus the
third party.

Verified live 2026-08-02:

    mustsharenews.com/?s=yishun&feed=rss2      -> 10 dated entries, 15 Yishun links
    theindependent.sg/?s=yishun&feed=rss2      -> 10 dated entries,  5 Yishun links

Mothership is WordPress-shaped (it serves /feed/) but is NOT covered here: it
ignores the `s` parameter entirely and returns byte-identical output for
`?s=yishun` and `?s=yishun&feed=rss2`, so there is nothing to search. Its
front-page feed remains its only channel.

Unlike news_sitemap.py this source needs no article fetch — the search feed
already carries a summary per entry, the same shape the primary RSS scrapers
consume.
"""

import logging
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

import feedparser

from ingestion.contracts import Candidate, SourceBlockedError, SourceUnavailableError
from scrapers import BROWSER_HEADERS, content_matches_keywords, strip_html

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 25
_READ_CAP = 2_000_000
_CONTENT_LIMIT = 3_000

# Query terms per source. A site-search on `?s=yishun` only returns articles the
# publisher's index matched on "yishun", so a story that names ONLY a subzone
# (Khatib, Chong Pang) is never in that feed and the downstream keyword filter
# never gets a chance at it. The subzone terms are searched separately and the
# results merged/deduped by link — a handful of extra round-trips (throttled and
# cached via polite_get) for the recall the single-term search structurally missed.
SEARCH_TERMS = ["yishun", "khatib", "chong pang"]

_BLOCK_MARKERS = (
    "unusual traffic", "/sorry/", "captcha", "recaptcha",
    "detected unusual", "automated queries", "not a robot",
)


class WordPressSearchSource:
    """DISCOVERY-role source over one WordPress site's search RSS feed."""

    enabled = True
    source_type = "msm"

    def __init__(self, name: str, source_name: str, base_url: str, *,
                 terms: list[str] = SEARCH_TERMS, enabled: bool = True):
        self.name = name
        self.source_name = source_name
        self.base_url = base_url.rstrip("/")
        self.terms = terms
        self.enabled = enabled

    def feed_url(self, term: str) -> str:
        return f"{self.base_url}/?s={urllib.parse.quote(term)}&feed=rss2"

    def fetch(self, since: date | None) -> list[Candidate]:
        """
        Search each term's feed and merge, deduped by link. `since` is advisory —
        the orchestrator re-applies RecencyFilter — and is used here only to avoid
        re-emitting entries the watermark already covers. Dateless entries are
        never dropped (INGESTION_DESIGN §5.1).
        """
        candidates: list[Candidate] = []
        seen: set[str] = set()
        for term in self.terms:
            self._fetch_term(term, since, seen, candidates)
        logger.info("%s: %d term(s) -> %d candidate(s)",
                    self.name, len(self.terms), len(candidates))
        return candidates

    def _fetch_term(self, term: str, since: date | None,
                    seen: set[str], candidates: list[Candidate]) -> None:
        url = self.feed_url(term)
        # Shares the per-host spacing, 403 back-off and per-pass cache with
        # every other publisher request — see scrapers.fetch_strategy.polite_get.
        # Same-host terms share the trip state, so once one 403s the rest fail
        # fast rather than hammering a rate-limited host.
        from scrapers.fetch_strategy import polite_get
        status, raw = polite_get(url, timeout=REQUEST_TIMEOUT, cap=_READ_CAP)
        if status in (403, 429):
            raise SourceBlockedError(f"{self.name}: HTTP {status}")
        if status == 0:
            raise SourceUnavailableError(f"{self.name}: fetch failed")
        if status != 200:
            raise SourceUnavailableError(f"{self.name}: HTTP {status}")

        snippet = raw[:4000].decode("utf-8", errors="ignore").lower()
        for marker in _BLOCK_MARKERS:
            if marker in snippet:
                raise SourceBlockedError(f"{self.name}: block-page marker {marker!r}")

        feed = feedparser.parse(raw)
        if feed.bozo and not feed.entries:
            raise SourceUnavailableError(f"{self.name}: search feed parse failed")

        skipped_stale = 0

        for entry in feed.entries:
            link = (entry.get("link") or "").strip()
            if not link or link in seen:
                continue

            title = (entry.get("title") or "").strip()
            summary = entry.get("summary", "") or entry.get("description", "")
            nodes = entry.get("content", [])
            body = strip_html(nodes[0].get("value", summary) if nodes else summary)

            # The site search is fuzzy — it will return articles that merely
            # mention a related term. Re-apply our own filter so the town
            # keyword really is present.
            if not content_matches_keywords(f"{title} {body} {link}"):
                continue

            published_at = None
            pp = entry.get("published_parsed")
            if pp:
                try:
                    published_at = date(*pp[:3])
                except (TypeError, ValueError):
                    published_at = None

            if since is not None and published_at is not None and published_at <= since:
                skipped_stale += 1
                continue

            seen.add(link)
            candidates.append(Candidate(
                title=title,
                content=body[:_CONTENT_LIMIT] or title,
                url=link,
                source_name=self.source_name,
                source_type="msm",
                published_at=published_at,
                discovered_via=self.name,
            ))

        logger.info("%s[%s]: %d feed entries, %d stale",
                    self.name, term, len(feed.entries), skipped_stale)


# ── Registry ─────────────────────────────────────────────────────────────────
# Publisher domains only. No aggregators, no redirect wrappers.

WP_SEARCH_SITES: list[tuple[str, str, str]] = [
    ("mustsharenews_search", "MustShareNews", "https://mustsharenews.com"),
    ("the_independent_search", "The Independent Singapore", "https://theindependent.sg"),
]


def wp_search_sources() -> list[WordPressSearchSource]:
    return [WordPressSearchSource(n, sn, u) for n, sn, u in WP_SEARCH_SITES]
