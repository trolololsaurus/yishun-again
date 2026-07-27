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
# Jittered backoff after a FAILED keyword query. Google News 503s from the Cloud
# Run datacenter IP ~3 days in 14; those are transient throttles, not bans, so we
# pause a little longer than the polite delay before the next keyword to let the
# throttle clear. Same shape as PER_KEYWORD_DELAY (a random.uniform tuple) and
# capped at one sleep per keyword over a fixed-length list, so the accumulated
# backoff can never blow the pass deadline.
BLOCK_BACKOFF = (5.0, 9.0)
REQUEST_TIMEOUT = 20

# Hard cap on redirect resolutions in one fetch. Steady state is ~50 (only
# post-watermark entries are resolved), so this only binds on a cold start or a
# watermark reset — the cases where an unbounded fetch would eat the whole pass.
MAX_RESOLVES_PER_FETCH = 120


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
        `since` cannot narrow the QUERY — Google News RSS date operators break
        the feed (smoke-test-proven) — but it does narrow how much work we do
        locally. See the resolution-order note below.

        Issues one "yishun {keyword}" query per entry in YISHUN_KEYWORDS,
        with a polite random delay between queries.

        Each keyword query is failure-isolated. Google News returns 503 from the
        Cloud Run datacenter IP intermittently (~3 days in 14), and this is the
        DOMINANT discovery channel — so a single failing query (503, block
        marker, network, parse error) must NOT discard the candidates already
        gathered from earlier keywords, which is exactly what a raise out of the
        bare loop used to do. We surface a SourceBlockedError only when EVERY
        query fails; a partial result is degraded, not dead.
        """
        candidates: list[Candidate] = []
        seen_urls: set[str] = set()
        seen_raw: set[str] = set()
        resolves = 0
        skipped_stale = 0

        # Distinguish "one flaky keyword" from "we are fully blocked": raise only
        # when nothing succeeded. last_error carries the reason into that raise.
        successes = 0
        failures = 0
        last_error: Exception | None = None

        for i, keyword in enumerate(YISHUN_KEYWORDS):
            query = f"yishun {keyword}"

            try:
                feed = _fetch_feed(query)

                for entry in feed.entries:
                    raw_url = entry.get("link", "").strip()
                    if not raw_url:
                        continue

                    # The same article surfaces under several keywords. Dedup on
                    # the wrapper before resolving; identical wrappers are common.
                    if raw_url in seen_raw:
                        continue
                    seen_raw.add(raw_url)

                    title = _entry_title(entry)
                    summary = strip_html(entry.get("summary", "") or entry.get("description", ""))
                    if not content_matches_keywords(f"{title} {summary}"):
                        continue

                    # ── Recency BEFORE redirect resolution ───────────────────
                    # _resolve_redirect is a network round-trip per entry, and
                    # this feed returns ~650 entries of which ~50 are newer than
                    # the watermark. Resolving first meant ~600 HTTP calls whose
                    # results RecencyFilter discarded seconds later — that alone
                    # consumed the entire 900s pass budget and starved the
                    # sources queued behind this one. The RSS entry already
                    # carries its own date, so staleness is knowable for free.
                    #
                    # Dateless entries are NEVER skipped here: routing them to
                    # review rather than dropping them is deliberate
                    # (INGESTION_DESIGN §5.1), and a missing pubDate must not
                    # become a silent delete.
                    published_at = _entry_published_at(entry)
                    if since is not None and published_at is not None and published_at <= since:
                        skipped_stale += 1
                        continue

                    if resolves >= MAX_RESOLVES_PER_FETCH:
                        # Safety valve, not an expected path: a watermark reset
                        # would otherwise make one pass try to resolve the whole
                        # feed.
                        logger.warning(
                            "google_news_rss: hit MAX_RESOLVES_PER_FETCH=%d — "
                            "%d candidate(s) left unresolved this pass",
                            MAX_RESOLVES_PER_FETCH, len(feed.entries),
                        )
                        break

                    real_url = _resolve_redirect(raw_url)
                    resolves += 1
                    if real_url in seen_urls:
                        continue
                    seen_urls.add(real_url)

                    candidates.append(Candidate(
                        title=title,
                        content=summary,
                        url=real_url,
                        source_name=_gnews_source_name(entry),
                        source_type="rss",
                        published_at=published_at,
                        discovered_via="google_news_rss",
                    ))

            except (SourceBlockedError, SourceUnavailableError) as exc:
                # Isolate the flaky keyword. Pre-fix this exception propagated out
                # of fetch() and threw away every candidate already collected from
                # earlier keywords — one 503 on keyword 3 also lost keywords 1–2
                # and blanked the dominant channel for the whole pass.
                failures += 1
                last_error = exc

                # A hard block on the very FIRST query, with nothing gathered yet,
                # is almost certainly a full datacenter-IP block (the same IP
                # serves all queries): fail fast instead of burning a backoff on
                # each remaining keyword to prove what the first block told us.
                if successes == 0 and i == 0 and isinstance(exc, SourceBlockedError):
                    raise

                logger.warning(
                    "google_news_rss: keyword %r failed (%s); %d/%d queries "
                    "failed so far — continuing with the rest",
                    keyword, exc, failures, i + 1,
                )

                # Transient throttle → jittered backoff before the next keyword.
                # One bounded sleep per keyword over a fixed-length list, so it
                # can never blow the pass deadline.
                if i < len(YISHUN_KEYWORDS) - 1:
                    time.sleep(random.uniform(*BLOCK_BACKOFF))
                continue

            successes += 1

            if resolves >= MAX_RESOLVES_PER_FETCH:
                break

            if i < len(YISHUN_KEYWORDS) - 1:
                time.sleep(random.uniform(*PER_KEYWORD_DELAY))

        # Every query failed → fully blocked/unavailable, not merely degraded.
        # Raise so fallback.py reacts: a silent [] here would read as "no Yishun
        # news" (the empty-result contract) and hide an outage of the dominant
        # discovery channel. Reported as a block per the failure-isolation rule.
        if successes == 0:
            raise SourceBlockedError(
                f"google_news_rss: all {len(YISHUN_KEYWORDS)} keyword queries "
                f"failed; last error: {last_error}"
            ) from last_error

        logger.info(
            "google_news_rss: %d candidate(s); %d resolved, %d stale entries "
            "skipped before resolution; %d/%d keyword queries failed",
            len(candidates), resolves, skipped_stale, failures, len(YISHUN_KEYWORDS),
        )
        return candidates
