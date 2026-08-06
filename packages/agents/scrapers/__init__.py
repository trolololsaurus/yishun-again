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

# Attribute ORDER is not guaranteed. These patterns originally required
# `property=...` before `content=...`, so a publisher emitting
# `<meta content="2026-07-29" property="article:published_time">` — valid HTML,
# and what several CMSs produce — matched nothing and the article went undated.
# Each meta key is therefore tried in both orders.
_PUB_META_KEYS = [
    "article:published_time",
    "og:published_time",
    # Mediacorp (CNA, Berita) ships this on pages that carry NO
    # article:published_time and no JSON-LD datePublished. It is the article's
    # own publish time, in SGT.
    "cXenseParse:recs:publishtime",
    "datePublished",
    "publishdate",
    "pubdate",
    "date",
    "DC.date.issued",
]
_PUB_META_PATTERNS = [
    # JSON-LD / any inline JSON — order-independent by construction.
    r'"datePublished"\s*:\s*"([^"]+)"',
    r'<time[^>]+datetime=["\']([^"\']+)',
] + [
    p
    for key in _PUB_META_KEYS
    for p in (
        rf'(?:property|name|itemprop)=["\']{re.escape(key)}["\'][^>]*?content=["\']([^"\']+)',
        rf'content=["\']([^"\']+)["\'][^>]*?(?:property|name|itemprop)=["\']{re.escape(key)}["\']',
    )
] + [
    # LAST RESORT, and it used to be second. `uploadDate` belongs to a
    # schema.org VideoObject — it dates the VIDEO FILE, not the article, and a
    # newsroom re-running an old clip stamps it with today.
    #
    # berita.mediacorp.sg's report on the 2016 Yishun Ring Road killing carries
    # no datePublished at all, two `"uploadDate": "2026-08-0*"` blocks for a
    # 30-minute clip, and the real date only in cXenseParse:recs:publishtime
    # (2016-08-15T20:35:48+08:00). Matching uploadDate first dated a ten-year-old
    # murder report TODAY, and the incident page printed that beside the link.
    r'"uploadDate"\s*:\s*"([^"]+)"',
]
# The day segment must be the WHOLE segment — `/2018/07/13/`, not the leading
# digits of a slug. This ended in `(?:/|\b)`, and `\b` is satisfied by the
# boundary between a digit and a hyphen, so Mothership's dateless
# `/2026/07/6-men-charged-yishun-rioting/` resolved to 2026-07-06 — a date
# eighteen days BEFORE the incident, printed on the published page beside the
# link. Mothership stamps only /YYYY/MM/ into its paths and its day is never
# there to read; the fetch rungs below are what find it.
# Matches apps/web `dateFromUrl`, which always required a full segment.
# Guard: test_url_date_extraction.py.
_URL_DATE_RE  = re.compile(r"/(\d{4})/(\d{1,2})/(\d{1,2})(?=[/?#]|$)")
_ISO_DATE_RE  = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_HTML_READ_CAP = 400_000   # bytes; the meta tags live in <head>


def _safe_date(y, m, d) -> date | None:
    try:
        return date(int(y), int(m), int(d))
    except (TypeError, ValueError):
        return None


# ── Human-readable dateline (last resort, before giving up) ──────────────────
#
# Some publishers ship NO machine-readable date and print it only as text.
# Mothership is the case that proved it:
#
#     <h3 class="text-sm pl-6">July 30, 2026, 11:30 AM</h3>
#
# No meta tag, no <time>, no JSON-LD — but the date is right there in the HTML.
# It was reported as "undated" on a published page because every pattern here
# assumed DAY-FIRST ("30 July 2026") and Mothership writes MONTH-FIRST. Both
# orders are matched now; the mistake was the regex, not the publisher.
#
# Validated against 10 Mothership articles with known RSS pubDates: 10/10 exact.
# (A rejected alternative — reading the cover-image CDN upload timestamp — was
# 8/10, and a date that is wrong 20% of the time is worse on a published page
# than an honest "Undated".)
#
# FIRST match wins, which is what makes this safe: publishers put the article's
# own dateline in the header, above the related-posts list whose entries carry
# their own later datelines.
_MONTH_NUM = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june",
     "july", "august", "september", "october", "november", "december"])}
_MONTH_NUM.update({m[:3]: n for m, n in list(_MONTH_NUM.items())})
_MONTH_NUM["sept"] = 9

_MONTH_NAMES = ("January|February|March|April|May|June|July|August|September|"
                "October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept|Sep|Oct|Nov|Dec")
_TEXT_DATE_MDY = re.compile(rf"\b({_MONTH_NAMES})\.?\s+(\d{{1,2}}),?\s+(\d{{4}})\b", re.I)
_TEXT_DATE_DMY = re.compile(rf"\b(\d{{1,2}})\s+({_MONTH_NAMES})\.?,?\s+(\d{{4}})\b", re.I)


def _date_from_text(html: str) -> date | None:
    """First human-readable dateline in `html`, in either month/day order."""
    for rx, month_first in ((_TEXT_DATE_MDY, True), (_TEXT_DATE_DMY, False)):
        hit = rx.search(html or "")
        if not hit:
            continue
        month_raw, day = (hit.group(1), hit.group(2)) if month_first \
            else (hit.group(2), hit.group(1))
        month = _MONTH_NUM.get(month_raw.lower().rstrip("."))
        if not month:
            continue
        if (found := _safe_date(hit.group(3), month, day)):
            return found
    return None


# ── Article body enrichment ──────────────────────────────────────────────────
#
# Several publishers put only a STANDFIRST in their RSS `<description>`, not the
# article. Measured live on 2026-08-05 over the first 6 entries of each feed:
#
#     straits_times     67-110 chars      <- one line
#     cna               64-175 chars      <- one line
#     yahoo             0 chars           <- nothing at all
#     mothership        1372-4910 chars   <- full body
#     mustsharenews     3031-7939 chars   <- full body
#     the_independent   2396-4482 chars   <- full body
#
# Stage 2 writes the incident summary from this text, so an ST/CNA/Yahoo story
# reached the queue with a headline and one sentence to work from. That is what
# produced the thin PMD-impound card: 108 characters of source for the whole
# incident. It also drags confidence down (0.30) and invites the model to invent
# specifics the groundedness check then has to catch.
#
# Fetching is deliberately conditional. The scrapers have ALREADY applied the
# Yishun keyword filter by the time this runs, so it costs one request for each
# candidate that is actually going to be processed — a handful a day across the
# whole fleet, not one per feed entry.
MIN_BODY_CHARS = 600

_BODY_CAP = 400_000


def article_body(url: str) -> str:
    """Fetch `url` and return its readable body text, or '' on any failure.

    Uses the same fallback ladder as date resolution (direct, then the Wayback
    snapshot), so a publisher that blocks the datacenter IP still yields text.
    """
    if not url:
        return ""
    try:
        from .fetch_strategy import fetch_with_fallback
        result = fetch_with_fallback(url)
        if not result or not result.html:
            return ""
        soup = BeautifulSoup(result.html, "lxml")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
            tag.decompose()
        paras = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
        return " ".join(p for p in paras if len(p) > 30)[:_BODY_CAP]
    except Exception as exc:                      # noqa: BLE001 — enrichment is best-effort
        logger.debug("article_body: failed for %s: %s", url[:90], exc)
        return ""


def enrich_thin_content(item: dict, *, minimum: int = MIN_BODY_CHARS) -> dict:
    """Return `item` with its `content` replaced by the article body when the
    feed supplied less than `minimum` characters.

    NEVER shrinks: if the fetch fails, or returns less text than the feed gave,
    the original content is kept. Degrading to a thin summary is bad; degrading
    to an empty one would be worse.
    """
    content = (item.get("content") or "").strip()
    if len(content) >= minimum:
        return item

    url = (item.get("url") or "").strip()
    if not url:
        return item

    # article_body() guards itself, but this call is wrapped too: enrichment is
    # an optimisation, and nothing about it may ever fail an ingestion pass.
    try:
        body = article_body(url)
    except Exception as exc:                      # noqa: BLE001
        logger.warning("enrich_thin_content: fetch raised for %s: %s", url[:90], exc)
        return item

    if len(body) > len(content):
        logger.info("enriched %s: %d -> %d chars",
                    (item.get("url") or "")[:80], len(content), len(body))
        return {**item, "content": body}
    logger.warning(
        "thin content kept for %s: feed gave %d chars and the article fetch "
        "added nothing — the draft will be written from very little",
        (item.get("url") or "")[:80], len(content))
    return item


def _date_from_html(html: str) -> date | None:
    """Extract a publication date from article HTML.

    Machine-readable metadata first (authoritative), then the visible dateline
    — never the other way round: a page's body text can mention many dates and
    only the meta tag is unambiguous.
    """
    for pat in _PUB_META_PATTERNS:
        hit = re.search(pat, html, re.IGNORECASE)
        if not hit:
            continue
        iso = _ISO_DATE_RE.match(hit.group(1).strip())
        if iso and (found := _safe_date(*iso.groups())):
            return found
    return _date_from_text(html)


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

    # 2. The article's own metadata. Routed through polite_get so it shares the
    #    per-host spacing, the 403 back-off AND the per-pass cache with every
    #    other publisher request. It used to fetch with its own urllib call and
    #    was therefore invisible to the throttle — and it commonly re-fetched an
    #    article the sitemap adapter had already pulled seconds earlier.
    html = None
    try:
        from .fetch_strategy import polite_get
        status, body = polite_get(url, timeout=timeout, cap=_HTML_READ_CAP)
        if status == 200 and body:
            html = body.decode("utf-8", errors="ignore")
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
