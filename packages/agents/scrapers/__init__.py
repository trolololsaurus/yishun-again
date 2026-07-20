"""
Scrapers package — shared constants, translation helpers, and scrape_all().
"""

import json
import logging
import os
import re
import time
import urllib.request
from datetime import date

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ── English keyword list (spec §4.1) ────────────────────────────────────────
YISHUN_KEYWORDS = [
    "yishun", "yishun ring road", "yishun ave", "yishun street",
    "yishun mrt", "northpoint", "khoo teck puat", "yishun park",
    "yishun dam", "yishun pond", "sembawang",
]

# ── Source-language keywords for pre-translation filtering ──────────────────
# Translate ONLY after a keyword match — never pre-emptively.
# "Yishun" appears in SG media of all languages, so always included.
_YISHUN_RAW: dict[str, list[str]] = {
    "zh": ["义顺", "Yishun", "yishun", "北点", "邱德拔"],  # Northpoint, KTP Hospital
    "ms": ["Yishun", "yishun", "Nee Soon"],               # Nee Soon = Malay name
    "ta": ["யிஷுன்", "Yishun", "yishun"],                  # Tamil transliteration
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
# They now raise. scrape_all() already wraps each scraper in try/except, so the
# best-effort legacy path is unchanged; ingestion's Source adapters translate
# these into SourceBlockedError / SourceUnavailableError, which FallbackLadder
# and the run report already understand.

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

    # 2. The article's own metadata.
    try:
        req = urllib.request.Request(url, headers=BROWSER_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read(_HTML_READ_CAP).decode("utf-8", errors="ignore")
    except Exception as exc:
        logger.debug("resolve_published_at: fetch failed for %s: %s", url[:80], exc)
        return None

    for pat in _PUB_META_PATTERNS:
        hit = re.search(pat, html, re.IGNORECASE)
        if not hit:
            continue
        iso = _ISO_DATE_RE.match(hit.group(1).strip())
        if iso and (found := _safe_date(*iso.groups())):
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


# ── Scraper health logging ────────────────────────────────────────────────────

def log_scraper_run(
    source_name: str,
    source_type: str,
    items_found: int,
    duration_ms: int,
    errors: list[str] | None = None,
    items_passed_s1: int = 0,
) -> None:
    """
    Log one row to scraper_health after each scraper run in scrape_all().
    Silently skips if Supabase is unconfigured or any DB call fails —
    health logging must never crash the pipeline.

    Status rules (spec §8a):
      error   — exception thrown (errors list is non-empty)
      warning — 0 items for 3+ consecutive runs, or duration > 3x 7d baseline
      ok      — everything else
    """
    from datetime import datetime, timezone, timedelta

    try:
        from classifiers.corroboration import get_supabase_client
        client = get_supabase_client()
    except (ImportError, EnvironmentError):
        logger.debug("Supabase not configured — skipping health log for %s", source_name)
        return
    except Exception as exc:
        logger.warning("Health log: Supabase connect failed for %s: %s", source_name, exc)
        return

    try:
        # Consecutive zeros: query last row for this source
        last = (
            client.table("scraper_health")
            .select("consecutive_zeros")
            .eq("source_name", source_name)
            .order("scraped_at", desc=True)
            .limit(1)
            .execute()
        )
        last_zeros = last.data[0]["consecutive_zeros"] if last.data else 0
        consecutive_zeros = 0 if items_found > 0 else (last_zeros + 1)

        # 7-day avg duration for slow-run detection
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        hist = (
            client.table("scraper_health")
            .select("duration_ms")
            .eq("source_name", source_name)
            .gte("scraped_at", cutoff)
            .execute()
        )
        durations = [
            r["duration_ms"] for r in (hist.data or [])
            if r.get("duration_ms") is not None
        ]
        avg_duration_7d = int(sum(durations) / len(durations)) if durations else None

        # Compute status
        if errors:
            status = "error"
            status_reason = "; ".join(errors[:3])
        elif consecutive_zeros >= 3:
            status = "warning"
            status_reason = f"0 items for {consecutive_zeros} consecutive runs"
        elif avg_duration_7d and duration_ms > 3 * avg_duration_7d:
            status = "warning"
            status_reason = (
                f"duration {duration_ms}ms is >3x 7d avg ({avg_duration_7d}ms)"
            )
        else:
            status = "ok"
            status_reason = None

        client.table("scraper_health").insert({
            "source_name":       source_name,
            "source_type":       source_type,
            "items_found":       items_found,
            "items_passed_s1":   items_passed_s1,
            "errors":            errors,
            "duration_ms":       duration_ms,
            "status":            status,
            "status_reason":     status_reason,
            "consecutive_zeros": consecutive_zeros,
            "avg_duration_7d":   avg_duration_7d,
        }).execute()
        logger.debug(
            "Health logged [%s]: status=%s items=%d duration=%dms",
            source_name, status, items_found, duration_ms,
        )

    except Exception as exc:
        logger.warning("Health log write failed for %s: %s", source_name, exc)


# ── Unified scrape_all ────────────────────────────────────────────────────────

def scrape_all() -> list[dict]:
    """
    Run all active scrapers and return a combined, URL-deduplicated list.

    Returns:
        List of dicts: {title, content, url, source_name, source_type}
        Multilingual items also include translated_from: "zh"|"ms"|"ta".
        source_type: "msm" | "reddit" | "signal"
    """
    from .scrape_cna           import scrape as scrape_cna
    from .scrape_mothership    import scrape as scrape_mothership
    from .scrape_straitstimes  import scrape as scrape_straitstimes
    from .scrape_mustsharenews import scrape as scrape_mustsharenews
    from .scrape_theindependent import scrape as scrape_theindependent
    from .scrape_yahoo         import scrape as scrape_yahoo
    from .scrape_asiaone       import scrape as scrape_asiaone
    from .scrape_stomp         import scrape as scrape_stomp
    from .scrape_zaobao        import scrape as scrape_zaobao
    from .scrape_shinmin       import scrape as scrape_shinmin
    from .scrape_beritaharian  import scrape as scrape_beritaharian
    from .scrape_tamilmurasu   import scrape as scrape_tamilmurasu
    from .scrape_reddit        import scrape as scrape_reddit
    from .scrape_edmw          import scrape as scrape_edmw

    scrapers = [
        # English MSM — RSS feeds
        ("CNA",                 scrape_cna,            "msm"),
        ("Mothership",          scrape_mothership,      "msm"),
        ("Straits Times",       scrape_straitstimes,    "msm"),
        ("MustShareNews",       scrape_mustsharenews,   "msm"),
        ("The Independent",     scrape_theindependent,  "msm"),
        ("Yahoo",               scrape_yahoo,           "msm"),
        ("AsiaOne",             scrape_asiaone,         "msm"),
        ("Stomp",               scrape_stomp,           "msm"),
        # Multilingual MSM — translate on match
        ("Zaobao",              scrape_zaobao,          "msm"),
        ("Shinmin",             scrape_shinmin,         "msm"),
        ("Berita Harian",       scrape_beritaharian,    "msm"),
        ("Tamil Murasu",        scrape_tamilmurasu,     "msm"),
        # Social / signal
        ("Reddit",              scrape_reddit,          "reddit"),
        ("EDMW",                scrape_edmw,            "signal"),
    ]

    all_items: list[dict] = []
    seen_urls: set[str]   = set()

    for name, fn, source_type in scrapers:
        t0 = time.monotonic()
        run_errors: list[str] = []
        new = 0
        try:
            items = fn()
            for item in items:
                url = item.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_items.append(item)
                    new += 1
            logger.info("scrape_all [%s]: %d new items", name, new)
        except Exception as exc:
            run_errors.append(str(exc))
            logger.error("scrape_all [%s] failed: %s", name, exc)

        duration_ms = int((time.monotonic() - t0) * 1000)
        log_scraper_run(
            source_name=name,
            source_type=source_type,
            items_found=new,
            duration_ms=duration_ms,
            errors=run_errors if run_errors else None,
        )
        time.sleep(0.5)

    logger.info("scrape_all total: %d unique items across all sources", len(all_items))
    return all_items
