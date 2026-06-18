"""
Google News RSS source adapter (INGESTION_DESIGN.md §3.2, §3.3) —
CORROBORATION role (Q1=1b).

Cross-checks the MSM-primary sources (e.g. msm/cna.py) against Google News
RSS coverage of the same Yishun keywords. This source's items are never
treated as the primary record — they corroborate, or surface items the
MSM-primary sources missed.

Query format is smoke-test-proven (scrapers/smoke_test.py): NO after:/before:
date operators — they break the feed (empty/garbage results, confirmed
empirically). Recency windowing is RecencyFilter's job (§5.1), not this
source's — `since` is accepted for Source protocol conformance only.

Block detection mirrors smoke_test.py::_classify_block: HTTP 429/403, or a
response body containing any BLOCK_MARKERS, raises SourceBlockedError.
Network/parse errors raise SourceUnavailableError. Neither is swallowed —
ingestion/fallback.py::run_with_fallback decides what happens next.
"""

import logging
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone

import feedparser

from ingestion.contracts import Candidate, SourceBlockedError, SourceUnavailableError
from scrapers import YISHUN_KEYWORDS, content_matches_keywords, strip_html
from scrapers._gnews_helpers import _gnews_source_name, _resolve_redirect

logger = logging.getLogger(__name__)

GNEWS_RSS = (
    "https://news.google.com/rss/search"
    "?q={q}&hl=en-SG&gl=SG&ceid=SG:en"
)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Same markers as scrapers/smoke_test.py::BLOCK_MARKERS
BLOCK_MARKERS = [
    "unusual traffic", "/sorry/", "captcha", "recaptcha",
    "detected unusual", "automated queries", "not a robot",
]

PER_KEYWORD_DELAY = (4.0, 6.0)  # polite random delay between keyword queries (s)
REQUEST_TIMEOUT = 20


def _classify_block(status: int | None, body_snippet: str) -> str | None:
    """Mirror smoke_test.py::_classify_block."""
    if status in (429, 403):
        return f"HTTP {status}"
    low = (body_snippet or "").lower()
    for marker in BLOCK_MARKERS:
        if marker in low:
            return f"block-page marker: '{marker}'"
    return None


def _fetch_feed(query: str):
    """
    One Google News RSS fetch for `query`. Checks status + body for block
    markers BEFORE parsing (a block page can still be HTTP 200).
    """
    url = GNEWS_RSS.format(q=urllib.parse.quote(query))
    req = urllib.request.Request(
        url,
        headers={"User-Agent": _UA, "Accept-Language": "en-SG,en;q=0.9"},
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            status = resp.getcode()
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        reason = _classify_block(exc.code, "")
        if reason:
            raise SourceBlockedError(f"google_news_rss: {reason} (query={query!r})") from exc
        raise SourceUnavailableError(f"google_news_rss: HTTP {exc.code} (query={query!r})") from exc
    except Exception as exc:
        raise SourceUnavailableError(f"google_news_rss: {type(exc).__name__}: {exc} (query={query!r})") from exc

    snippet = raw[:4000].decode("utf-8", errors="ignore")
    reason = _classify_block(status, snippet)
    if reason:
        raise SourceBlockedError(f"google_news_rss: {reason} (query={query!r})")

    return feedparser.parse(raw)


def _entry_published_at(entry) -> date | None:
    pp = entry.get("published_parsed")
    if not pp:
        return None
    try:
        return datetime(*pp[:6], tzinfo=timezone.utc).date()
    except Exception:
        return None


def _entry_title(entry) -> str:
    """Strip Google News' ' - Source Name' suffix from the title."""
    title = entry.get("title", "").strip()
    if " - " in title:
        title = title.rsplit(" - ", 1)[0].strip()
    return title


class GoogleNewsRSSSource:
    """CORROBORATION-role source (§3.3, Q1=1b)."""

    name = "google_news_rss"
    enabled = True

    def fetch(self, since: date | None) -> list[Candidate]:
        """
        `since` is accepted for Source protocol conformance but not used to
        narrow the query — Google News RSS date operators break the feed
        (smoke-test-proven). RecencyFilter (§5.1) applies the real window
        downstream.

        Issues one "yishun {keyword}" query per entry in YISHUN_KEYWORDS,
        with a polite random delay between queries.
        """
        candidates: list[Candidate] = []
        seen_urls: set[str] = set()

        for i, keyword in enumerate(YISHUN_KEYWORDS):
            query = f"yishun {keyword}"
            feed = _fetch_feed(query)

            for entry in feed.entries:
                raw_url = entry.get("link", "").strip()
                if not raw_url:
                    continue

                title = _entry_title(entry)
                summary = strip_html(entry.get("summary", "") or entry.get("description", ""))
                if not content_matches_keywords(f"{title} {summary}"):
                    continue

                real_url = _resolve_redirect(raw_url)
                if real_url in seen_urls:
                    continue
                seen_urls.add(real_url)

                candidates.append(Candidate(
                    title=title,
                    content=summary,
                    url=real_url,
                    source_name=_gnews_source_name(entry),
                    source_type="rss",
                    published_at=_entry_published_at(entry),
                    discovered_via="google_news_rss",
                ))

            if i < len(YISHUN_KEYWORDS) - 1:
                time.sleep(random.uniform(*PER_KEYWORD_DELAY))

        return candidates
