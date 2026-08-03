"""
Scrapers package — shared constants, date resolution, and translation helpers.

The per-source `scrape_*` modules live here; the LIVE pipeline drives them
through the `ingestion/sources/` adapters, not from this package. A unified
`scrape_all()` used to exist alongside them and was deleted in July 2026: it had
had no callers since the adapter port, but it still labelled Reddit
`source_type='reddit'` (the pre-July vocabulary — Reddit is a `'signal'` now)
and it was the only writer of `scraper_health`. Both were traps: the first
because a future caller would have quietly breached guardrail #2, the second
because the health table it fed went stale where the supervisor and War Room
still read it. Health rows are now written by `ingestion/health.py` from the
real pass.
"""

import json
import logging
import os
import re
import socket
import urllib.request
from datetime import date

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# feedparser.parse(<url>) fetches via urllib with NO timeout — one wedged TCP
# connection to a feed host would hang the pass past the orchestrator's
# deadline (its checks run between fetches and can't interrupt one). This
# default applies only to sockets without an explicit timeout; httpx calls
# set their own and are unaffected.
socket.setdefaulttimeout(30)

# ── English keyword list (spec §4.1) ────────────────────────────────────────
#
# SCOPE RULE: a keyword qualifies only if it names the Yishun planning area or
# something inside it. Adjacent towns do NOT qualify, however close they are.
#
# "sembawang" was in this list until 2026-08-02 and should never have been.
# Sembawang is its own URA planning area with its own town centre — it is not
# Yishun and never was. Every TechSpec from v1.5 onward carried the line
# `# NOTE: "sembawang" removed — separate town, not Yishun`, but the code was
# never actually changed, so the spec and the filter disagreed for months. The
# cost was real: it pulled "19-year-old arrested for plotting knife attacks on
# Sembawang Air Base soldiers" into the queue, which the operator then had to
# reject by hand. Do not re-add it, and do not add Woodlands, Admiralty,
# Canberra or Sembawang Hills for the same reason.
#
# The subzone names below are all inside the Yishun planning area:
#   khatib      — Khatib subzone / Khatib MRT (NS14)
#   chong pang  — Chong Pang subzone, north-west Yishun
# Matching is plain case-insensitive substring, so the bare "yishun" entry
# already covers "Yishun Ring Road", "Yishun Ave 6", "Yishun MRT" and friends;
# only names that do NOT contain "yishun" need their own entry.
#
# "nee soon" is deliberately NOT here even though the subzone is Yishun. In
# news copy it is overwhelmingly the CONSTITUENCY (Nee Soon GRC), not the
# place: measured against The Independent's search feed on 2026-08-02 its only
# hit was an article about an MP, which guardrail #4 has to reject as political
# content anyway. Every genuine Yishun story in that same sample already
# matched on "yishun", so it bought nothing and cost a banned-category
# candidate. It stays in the Malay list below, where it is a place-name.
YISHUN_KEYWORDS = [
    "yishun",
    "khatib",
    "chong pang",
    "northpoint",       # Northpoint City, the town mall
    "khoo teck puat",   # KTP Hospital, Yishun Central
]

# ── Source-language keywords for pre-translation filtering ──────────────────
# Translate ONLY after a keyword match — never pre-emptively.
# "Yishun" appears in SG media of all languages, so always included.
# Same scope rule as YISHUN_KEYWORDS: planning area only, no adjacent towns.
_YISHUN_RAW: dict[str, list[str]] = {
    # 义顺 Yishun, 卡迪 Khatib, 忠邦 Chong Pang, 北点 Northpoint, 邱德拔 KTP Hospital
    "zh": ["义顺", "Yishun", "yishun", "卡迪", "忠邦", "北点", "邱德拔"],
    # Nee Soon = the Malay/historical name for the town
    "ms": ["Yishun", "yishun", "Nee Soon", "nee soon", "Khatib", "khatib",
           "Chong Pang", "chong pang"],
    "ta": ["யிஷுன்", "Yishun", "yishun", "கத்திப்", "Khatib", "khatib"],
}

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-SG,en;q=0.9",
}


# ── Failure signalling ───────────────────────────────────────────────────────
# The scrapers used to catch everything and return [], so a dead source was
# indistinguishable from "no Yishun news today". Stomp proved the cost: its
# search endpoint had moved, every run logged "skipping run" and returned zero,
# and nothing ever surfaced it.
#
# They now raise. Ingestion's Source adapters translate these into
# SourceBlockedError / SourceUnavailableError, which FallbackLadder, the run
# report and the scraper_health row all already understand.

class ScraperError(Exception):
    """A scraper could not complete its run (transport, HTTP, or parse failure)."""


class ScraperBlocked(ScraperError):
    """Bot-detection or rate-limiting — back off rather than retry immediately."""


_BLOCK_MARKERS = (
    "403", "429", "forbidden", "too many requests", "captcha", "recaptcha",
    "unusual traffic", "/sorry/", "automated queries", "not a robot",
)


def raise_scrape_failure(source: str, exc: Exception) -> None:
    """
    Re-raise a scraper's internal failure as a typed error.

    Classifies bot-detection/rate-limit as ScraperBlocked so the adapter can map
    it to SourceBlockedError; everything else is a transient ScraperError.
    Always raises — never returns.
    """
    blob = f"{type(exc).__name__}: {exc}".lower()
    if any(marker in blob for marker in _BLOCK_MARKERS):
        raise ScraperBlocked(f"{source}: {exc}") from exc
    raise ScraperError(f"{source}: {exc}") from exc


# ── Keyword matching ─────────────────────────────────────────────────────────

def content_matches_keywords(text: str) -> bool:
    """Return True if any YISHUN_KEYWORDS appear in text (case-insensitive)."""
    text_lower = text.lower()
    return any(kw in text_lower for kw in YISHUN_KEYWORDS)


def content_matches_lang(text: str, lang: str) -> bool:
    """
    Return True if any source-language Yishun keywords appear in text.
    Used to pre-filter before making translation API calls.
    Case-insensitive for ASCII keywords; exact match for non-ASCII scripts.
    """
    text_lower = text.lower()
    for kw in _YISHUN_RAW.get(lang, []):
        if kw.lower() in text_lower:
            return True
    return False


# ── HTML stripping ────────────────────────────────────────────────────────────

def strip_html(text: str) -> str:
    """Strip HTML tags and collapse whitespace."""
    if not text or "<" not in text:
        return (text or "").strip()
    return BeautifulSoup(text, "lxml").get_text(separator=" ", strip=True)


# ── Article publication-date resolution ──────────────────────────────────────
# The HTML-scraped sources (AsiaOne, Stomp, Zaobao, Shin Min, Berita Harian,
# Tamil Murasu) list articles without dates. A candidate with no published_at is
# "dateless": it bypasses the recency watermark, is re-processed by Stage 1/2 on
# every pass, and cannot be approved until an operator sets the date by hand
# (QA H3). Resolving the date is therefore the gate on using those sources live.
#
# Same precedence seed_backfill.fetch_article uses: the URL path first (free, no
# request), then the article's own meta tags. Deliberately NO LLM fallback —
# this runs on every live pass, unlike the one-off backfill.

_PUB_META_PATTERNS = [
    r'property=["\']article:published_time["\'][^>]+content=["\']([^"\']+)',
    r'"datePublished"\s*:\s*"([^"]+)"',
    r'itemprop=["\']datePublished["\'][^>]+content=["\']([^"\']+)',
    r'<time[^>]+datetime=["\']([^"\']+)',
]
_URL_DATE_RE  = re.compile(r"/(\d{4})/(\d{1,2})/(\d{1,2})(?:/|\b)")
_ISO_DATE_RE  = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_HTML_READ_CAP = 400_000   # bytes; the meta tags live in <head>


def _safe_date(y, m, d) -> date | None:
    try:
        return date(int(y), int(m), int(d))
    except (TypeError, ValueError):
        return None


def _date_from_html(html: str) -> date | None:
    """Extract a publication date from article HTML via the meta patterns.
    Shared between the direct fetch and the Wayback fallback so both read the
    date the same way."""
    for pat in _PUB_META_PATTERNS:
        hit = re.search(pat, html, re.IGNORECASE)
        if not hit:
            continue
        iso = _ISO_DATE_RE.match(hit.group(1).strip())
        if iso and (found := _safe_date(*iso.groups())):
            return found
    return None


def _wayback_html(url: str) -> str | None:
    """Newest Wayback snapshot HTML for `url`, or None. Never raises — a fetch
    failure just yields None and the caller treats the candidate as dateless."""
    try:
        from .fetch_strategy import WaybackSnapshot
        result = WaybackSnapshot().fetch(url)
    except Exception as exc:  # import or fetch failure must not crash the pass
        logger.debug("resolve_published_at: wayback fallback failed for %s: %s", url[:80], exc)
        return None
    return result.html if result else None


def resolve_published_at(url: str, *, timeout: int = 10) -> date | None:
    """
    Best-effort publication date for an article URL.

    Returns a date, or None when it genuinely cannot be determined. NEVER
    raises: a failure yields None and the candidate is treated as dateless —
    routed to review, never dropped (RecencyFilter §5.1, Q2=2b).
    """
    if not url:
        return None

    # 1. Date in the URL path (e.g. /2026/07/16/) — no request needed.
    m = _URL_DATE_RE.search(url)
    if m and (found := _safe_date(*m.groups())):
        return found

    # 2. The article's own metadata (direct fetch).
    html = None
    try:
        req = urllib.request.Request(url, headers=BROWSER_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read(_HTML_READ_CAP).decode("utf-8", errors="ignore")
    except Exception as exc:
        logger.debug("resolve_published_at: direct fetch failed for %s: %s", url[:80], exc)

    if html and (found := _date_from_html(html)):
        return found

    # 3. Direct fetch was blocked or carried no date — fall back to the newest
    #    Wayback snapshot before giving up. Recovering the date here keeps the
    #    candidate from being stranded dateless-and-unapprovable (QA H3) just
    #    because the live article is behind a bot wall.
    archived = _wayback_html(url)
    if archived and (found := _date_from_html(archived)):
        return found

    logger.debug("resolve_published_at: no date found for %s", url[:80])
    return None


# ── Translation helper ────────────────────────────────────────────────────────

def translate_article(title: str, content: str, source_lang: str) -> tuple[str, str]:
    """
    Translate an article's title and content to English using Claude Haiku.
    Called ONLY after a source-language keyword match — never pre-emptively.

    Args:
        title:       Article title in source language.
        content:     Article body in source language (truncated before sending).
        source_lang: ISO 639-1 code: "zh", "ms", or "ta".

    Returns:
        (english_title, english_content)

    Raises:
        EnvironmentError: if ANTHROPIC_API_KEY is not set.
        Exception:        on API or parse failure — caller should catch and skip.
    """
    import anthropic  # lazy import — only needed for multilingual scrapers

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY not set")

    _LANG_NAMES = {"zh": "Chinese (Simplified)", "ms": "Malay", "ta": "Tamil"}
    lang_name = _LANG_NAMES.get(source_lang, source_lang)

    client = anthropic.Anthropic(api_key=api_key)

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        temperature=0.1,
        system=(
            "You translate news article titles and content to English. "
            'Return JSON only with exactly two keys: {"title": "...", "content": "..."}. '
            "No commentary. No markdown."
        ),
        messages=[{
            "role": "user",
            "content": (
                f"Translate this {lang_name} article to English:\n\n"
                f"TITLE: {title}\n\n"
                f"CONTENT: {content[:2000]}"
            ),
        }],
    )

    raw = response.content[0].text.strip()
    raw = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()
    result = json.loads(raw)
    return str(result.get("title", title)), str(result.get("content", content))
