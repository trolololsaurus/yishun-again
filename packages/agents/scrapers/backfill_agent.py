"""
Historical backfill agent — spec §14c.

Scrapes Google News (2015–2026) and Wikipedia (1980–2014) for historical
Yishun incidents, runs each candidate through the standard Stage 1 → Stage 2
→ consolidation pipeline, then applies auto-publish tiers:

  confidence >= 0.70   → direct insert to incidents (is_published=TRUE)
  confidence 0.50–0.69 → war_room_queue status='pending' for operator review
  confidence < 0.50    → silent reject, logged to scraper_health only

UPDATE matches from consolidation always go to war_room_queue regardless of
confidence — operator must always review updates (spec §14c).

Wikipedia treatment (spec §14c):
  - source_type = 'reference'
  - hype_meter = 1
  - is_developing = FALSE always
  - confidence floor: 0.60 if the article has a Wikipedia URL
  - latest_source_role = 'initial'

DRY RUN — AI calls run normally; nothing written to Supabase:
    python packages/agents/scrapers/backfill_agent.py --dry-run

LIVE RUN:
    python packages/agents/scrapers/backfill_agent.py

Max 500 items per run. Re-run to continue.

FastAPI endpoint: POST /backfill/run (registered in main.py)
"""

import logging
import re
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Optional

import feedparser
import httpx
from pathlib import Path
from dotenv import load_dotenv

from filters.stage1_quota import Stage1DailyQuota, Stage1HaltError
from scrapers._gnews_helpers import _gnews_source_name, _resolve_redirect

# Explicit path so the module finds .env regardless of CWD
# scrapers/backfill_agent.py → packages/agents/scrapers → packages/agents → packages → repo root
_repo_root = Path(__file__).resolve().parents[3]
load_dotenv(_repo_root / ".env", override=False)

logger = logging.getLogger(__name__)


# ── Constants ────────────────────────────────────────────────────────────────

# Google News RSS — date range operators in query (best-effort; filtered by year post-parse)
GNEWS_RSS = (
    "https://news.google.com/rss/search"
    "?q={query}&hl=en-SG&gl=SG&ceid=SG:en"
)

WIKI_API_URL = "https://en.wikipedia.org/w/api.php"

# Known Wikipedia articles to always check (spec §14c)
WIKI_ARTICLES = [
    "Yishun",
    "Crime in Singapore",
]

# Keywords for backfill — shorter compounds for broader historical recall
BACKFILL_KEYWORDS = [
    "yishun",
    "northpoint yishun",
    "khoo teck puat",
    "nee soon",
    "yishun mrt",
    "yishun park",
    "yishun dam",
]

# Auto-publish confidence tiers (spec §14c Option 2)
TIER_AUTO_PUBLISH = 0.70    # >= 0.70 → direct publish
TIER_QUEUE        = 0.50    # 0.50–0.69 → operator review
# < 0.50 → silent reject

# Wikipedia confidence floor (spec §14c)
WIKI_CONFIDENCE_FLOOR = 0.60

# Rate limiting
GNEWS_RATE_LIMIT  = 1.5    # seconds between Google News RSS requests
STAGE2_RATE_LIMIT = 1.5    # seconds between Stage 2 calls (Anthropic rate limits)
WIKI_RATE_LIMIT   = 1.5    # seconds between Wikipedia API calls

MAX_ITEMS_PER_RUN = 500    # hard cap — operator re-runs to get more

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Identifies us honestly per the Wikimedia API User-Agent policy (distinct
# from _BROWSER_UA, which Google News / link-validation calls keep using).
WIKI_UA = "YishunAgain/1.0 (https://yishunagain.com; bot@yishunagain.com)"

# Wikipedia scope: 1980–2014 (Google News covers 2015–2026)
WIKI_YEAR_MIN = 1980
WIKI_YEAR_MAX = 2023

# External-link domains to drop from citation candidates — Wikipedia's own
# infrastructure and sister projects, never a "real news source" (CHANGE 3).
WIKI_EXTLINKS_BLOCKLIST = (
    "wikipedia.org",
    "wikimedia.org",
    "wikidata.org",
    "wiktionary.org",
    "wikibooks.org",
    "wikiquote.org",
    "wikisource.org",
    "wikinews.org",
    "wikiversity.org",
    "wikivoyage.org",
    "mediawiki.org",
)

# Title keywords for filtering followed wikilinks (CHANGE 4) — keep only
# links plausibly relevant to Yishun / Singapore crime incidents.
WIKI_LINK_KEYWORDS = (
    "yishun", "murder", "killing", "stabbing", "crime",
    "manslaughter", "assault", "sentenced", "executed", "hanged",
)

# Hard cap on linked articles followed across the whole Wikipedia phase
# (CHANGE 4) — prevents runaway crawling via chained wikilinks.
WIKI_MAX_FOLLOWED_ARTICLES = 30


# ── Cheap local pre-filter (runs before Stage 1 — zero API cost) ─────────────
#
# Title-level keyword patterns that are almost always noise for this archive.
# Any item whose title contains one of these *and* contains NO override keyword
# is silently dropped before Stage 1, saving Stage 1 quota.
#
# Override keywords: if any appear in title+content the item is never pre-rejected
# (same list used by stage1_filter._has_override_keyword).
_PREFILTER_NOISE_PHRASES = (
    # Property / real estate
    "bto", "psf", "showflat", "property review", "en bloc", "en-bloc",
    "hdb resale", "condo launch", "new launch", "price guide",
    "floor plan", "rental yield",
    # Food / F&B (no incident)
    "food review", "cafe review", "restaurant review", "best food",
    "must try", "hawker guide", "food trail", "now open", "opening soon",
    "soft open", "grand open",
    # COVID / pandemic (nationwide, not Yishun-specific incident)
    "covid", "coronavirus", "pandemic", "vaccination", "vaccine",
    "booster", "circuit breaker", "phase 2", "safe management",
    # Infrastructure (no incident)
    "bus route", "mrt upgrade", "road works", "road closure",
    "construction update", "station upgrade",
    # Obituaries
    "in memoriam", "obituary", "death notice", "rest in peace",
    "passed away peacefully", "condolences",
)

# Incident-signal overrides — mirror stage1_filter._OVERRIDE_KEYWORDS
# (duplicated here so backfill_agent has no import-time dep on that module)
_PREFILTER_OVERRIDES = (
    "death", "dead", "died", "killed", "murder", "stab", "injur",
    "accident", "crash", "arrest", "charged", "jailed", "convicted",
    "sentenced", "poison", "outbreak", "recall", "unsafe", "assault",
    "attack", "fire", "explosion", "flood", "collapse", "abuse",
    "missing", "found dead",
)


def _prefilter_is_noise(item: dict) -> bool:
    """
    Return True if the item is cheap-detectable noise and should be dropped
    before spending a Stage 1 call on it.

    Logic:
      1. Check title (lower-case) for any _PREFILTER_NOISE_PHRASES.
      2. If none match → not noise (return False immediately).
      3. If a phrase matches, check title+content for any _PREFILTER_OVERRIDES.
      4. If an override is present → not noise (incident signal wins).
      5. Otherwise → noise (return True).
    """
    title_lower = item.get("title", "").lower()

    # Step 1: any noise phrase in the title?
    matched_phrase = next(
        (p for p in _PREFILTER_NOISE_PHRASES if p in title_lower), None
    )
    if matched_phrase is None:
        return False   # title is clean — don't pre-reject

    # Step 2: any override keyword in title+content?
    body = title_lower + " " + item.get("content", "").lower()
    if any(kw in body for kw in _PREFILTER_OVERRIDES):
        logger.debug(
            "Pre-filter: noise phrase %r found but override present — keeping: %s",
            matched_phrase, item.get("title", "")[:70],
        )
        return False   # incident signal overrides the noise phrase

    logger.debug(
        "Pre-filter REJECT [%s]: noise phrase %r in title: %s",
        item.get("source_name", "?"), matched_phrase, item.get("title", "")[:70],
    )
    return True


# ── Google News helpers ──────────────────────────────────────────────────────
# _gnews_source_name, _resolve_redirect now live in scrapers/_gnews_helpers.py
# (shared with ingestion/sources/google_news_rss.py) — imported above.

def _gnews_pub_date(entry) -> Optional[str]:
    """Return ISO date string from feedparser's published_parsed, or None."""
    pp = entry.get("published_parsed")
    if pp:
        try:
            return f"{pp.tm_year:04d}-{pp.tm_mon:02d}-{pp.tm_mday:02d}"
        except Exception:
            pass
    return None


def _scrape_gnews_year(keyword: str, year: int, seen_urls: set) -> list:
    """
    Fetch Google News RSS for keyword+year combination.
    Post-filters by year using the parsed publication date.
    Returns a list of candidate dicts: {title, content, url, source_name, source_type, date}.
    """
    from scrapers import strip_html, content_matches_keywords

    # Date range operators in search query; Google News RSS honours these approximately
    end = "2026-05-31" if year == 2026 else f"{year}-12-31"
    query = f"yishun {keyword} after:{year}-01-01 before:{end}"
    feed_url = GNEWS_RSS.format(query=urllib.parse.quote(query))

    try:
        feedparser.USER_AGENT = _BROWSER_UA
        feed = feedparser.parse(feed_url)
    except Exception as exc:
        logger.warning("GNews RSS parse error [%s %d]: %s", keyword, year, exc)
        return []

    items = []
    for entry in feed.entries:
        raw_url = entry.get("link", "").strip()
        if not raw_url:
            continue

        # Filter by year when parse date is available
        pub_date = _gnews_pub_date(entry)
        if pub_date:
            try:
                if int(pub_date[:4]) != year:
                    continue
            except ValueError:
                pass

        # Strip " - Source Name" suffix Google News appends to titles
        title = entry.get("title", "").strip()
        if " - " in title:
            title = title.rsplit(" - ", 1)[0].strip()

        summary = strip_html(entry.get("summary", "") or entry.get("description", ""))

        if not content_matches_keywords(f"{title} {summary}"):
            continue

        # Follow redirect to get the canonical article URL for dedup
        real_url = _resolve_redirect(raw_url)
        if real_url in seen_urls:
            continue

        seen_urls.add(real_url)
        items.append({
            "title":       title,
            "content":     summary,
            "url":         real_url,
            "source_name": _gnews_source_name(entry),
            "source_type": "msm",
            "date":        pub_date or f"{year}-07-01",
        })

    logger.debug(
        "GNews [%s %d]: %d new items from %d entries",
        keyword, year, len(items), len(feed.entries),
    )
    return items


# ── Wikipedia helpers ─────────────────────────────────────────────────────────

def _wiki_article_text(title: str) -> Optional[str]:
    """Fetch plain-text extract of a Wikipedia article via the MediaWiki API."""
    params = {
        "action":      "query",
        "titles":      title,
        "prop":        "extracts",
        "explaintext": True,
        "format":      "json",
        "redirects":   True,
    }
    try:
        resp = httpx.get(
            WIKI_API_URL,
            params=params,
            timeout=15.0,
            headers={"User-Agent": WIKI_UA},
        )
        resp.raise_for_status()
        pages = resp.json().get("query", {}).get("pages", {})
        for page in pages.values():
            return page.get("extract", "")
    except Exception as exc:
        logger.warning("Wikipedia article fetch error [%s]: %s", title, exc)
    return None


def _wiki_search(query: str = "Yishun Singapore incident", max_results: int = 15) -> list:
    """Search Wikipedia and return list of {title, snippet} dicts."""
    params = {
        "action":   "query",
        "list":     "search",
        "srsearch": query,
        "srlimit":  max_results,
        "format":   "json",
    }
    try:
        resp = httpx.get(
            WIKI_API_URL,
            params=params,
            timeout=15.0,
            headers={"User-Agent": WIKI_UA},
        )
        resp.raise_for_status()
        return resp.json().get("query", {}).get("search", [])
    except Exception as exc:
        logger.warning("Wikipedia search error: %s", exc)
    return []


def _wiki_external_links(title: str) -> list:
    """
    Fetch external links from a Wikipedia article via the MediaWiki API.
    Returns a list of URL strings. Empty list on failure.

    Uses: action=query, prop=extlinks, ellimit=50
    Filters out: Wikipedia-internal URLs, Commons URLs, Wikidata URLs.
    Keeps: .sg domains (CNA, ST, Mothership, AsiaOne, etc), BBC, Reuters,
           AFP, Guardian. Anything that looks like a real news source.
    """
    params = {
        "action":  "query",
        "titles":  title,
        "prop":    "extlinks",
        "ellimit": 50,
        "format":  "json",
    }
    links: list = []
    try:
        resp = httpx.get(
            WIKI_API_URL,
            params=params,
            timeout=15.0,
            headers={"User-Agent": WIKI_UA},
        )
        resp.raise_for_status()
        pages = resp.json().get("query", {}).get("pages", {})
        for page in pages.values():
            for entry in page.get("extlinks", []):
                url = entry.get("*", "")
                if not url or not url.startswith(("http://", "https://")):
                    continue
                host = urllib.parse.urlparse(url).netloc.lower()
                if any(blocked in host for blocked in WIKI_EXTLINKS_BLOCKLIST):
                    continue
                links.append(url)
    except Exception as exc:
        logger.warning("Wikipedia external links fetch error [%s]: %s", title, exc)
    return links


def _wiki_internal_links(title: str, max_links: int = 20) -> list:
    """
    Fetch internal wikilinks from a Wikipedia article via the MediaWiki API.
    Returns a list of article titles (strings). Empty list on failure.

    Uses: action=query, prop=links, pllimit=50
    Filters: keep only links whose titles contain "Yishun" OR match known
    Singapore crime/incident patterns (title contains any of: "murder",
    "killing", "stabbing", "crime", "Singapore", "manslaughter", "assault",
    "sentenced", "executed", "hanged").
    Returns at most max_links titles.
    """
    params = {
        "action":  "query",
        "titles":  title,
        "prop":    "links",
        "pllimit": 50,
        "format":  "json",
    }
    matched: list = []
    try:
        resp = httpx.get(
            WIKI_API_URL,
            params=params,
            timeout=15.0,
            headers={"User-Agent": WIKI_UA},
        )
        resp.raise_for_status()
        pages = resp.json().get("query", {}).get("pages", {})
        for page in pages.values():
            for entry in page.get("links", []):
                link_title = entry.get("title", "")
                if not link_title:
                    continue
                lowered = link_title.lower()
                if any(kw in lowered for kw in WIKI_LINK_KEYWORDS):
                    matched.append(link_title)
                    if len(matched) >= max_links:
                        return matched
    except Exception as exc:
        logger.warning("Wikipedia internal links fetch error [%s]: %s", title, exc)
    return matched


def _extract_wiki_candidates(text: str, article_title: str, seen_urls: set, external_links: list) -> list:
    """
    Split Wikipedia article text into paragraphs, extract those mentioning Yishun
    with a year in the WIKI_YEAR_MIN–WIKI_YEAR_MAX range.

    Each qualifying paragraph becomes a separate candidate.
    Returns list of candidate dicts.
    """
    wiki_url = f"https://en.wikipedia.org/wiki/{article_title.replace(' ', '_')}"
    candidates = []

    for para in text.split("\n\n"):
        para = para.strip()
        if len(para) < 120:
            continue
        if "yishun" not in para.lower():
            continue

        # Extract year — must be in Wikipedia scope
        year_match = re.search(r"\b(19[89]\d|20[01]\d)\b", para)
        if not year_match:
            continue
        year = int(year_match.group())
        if year < WIKI_YEAR_MIN or year > WIKI_YEAR_MAX:
            continue

        # Synthetic title from first sentence, capped at 120 chars
        first_sentence = para.split(".")[0].strip()
        if len(first_sentence) > 120:
            first_sentence = first_sentence[:117] + "..."

        title = f"{first_sentence} (Wikipedia)"

        # Dedup key: URL + para fingerprint (first 60 chars)
        dedup_key = f"{wiki_url}::{para[:60]}"
        if dedup_key in seen_urls:
            continue
        seen_urls.add(dedup_key)

        candidates.append({
            "title":              title,
            "content":            para[:3000],
            "url":                wiki_url,
            "source_name":        "Wikipedia",
            "source_type":        "reference",
            "date":               f"{year}-01-01",
            "external_citations": external_links,
        })

    return candidates


def _scrape_wikipedia(seen_urls: set) -> list:
    """
    Fetch Wikipedia content for historical Yishun incidents (1980–2014).
    Returns list of candidate dicts.
    """
    from scrapers import content_matches_keywords

    all_candidates = []
    processed_article_urls: set = set()
    fetched_titles: list = []     # titles successfully pulled in Steps 1–2 — wikilink seeds for Step 3
    followed_count = 0            # CHANGE 4: global counter, capped at WIKI_MAX_FOLLOWED_ARTICLES

    # Step 1: spec-mandated known articles
    for article_title in WIKI_ARTICLES:
        wiki_url = f"https://en.wikipedia.org/wiki/{article_title.replace(' ', '_')}"
        if wiki_url in processed_article_urls:
            continue
        processed_article_urls.add(wiki_url)

        text = _wiki_article_text(article_title)
        if not text:
            continue
        time.sleep(WIKI_RATE_LIMIT)

        external_links = _wiki_external_links(article_title)
        time.sleep(WIKI_RATE_LIMIT)

        candidates = _extract_wiki_candidates(text, article_title, seen_urls, external_links)
        logger.debug("Wikipedia [%s]: %d Yishun sections", article_title, len(candidates))
        all_candidates.extend(candidates)
        fetched_titles.append(article_title)

    # Step 2: search-discovered articles
    for result in _wiki_search(max_results=15):
        title = result.get("title", "")
        if not title:
            continue
        wiki_url = f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}"
        if wiki_url in processed_article_urls:
            continue
        processed_article_urls.add(wiki_url)

        text = _wiki_article_text(title)
        if not text or "yishun" not in text.lower():
            continue
        time.sleep(WIKI_RATE_LIMIT)

        external_links = _wiki_external_links(title)
        time.sleep(WIKI_RATE_LIMIT)

        candidates = _extract_wiki_candidates(text, title, seen_urls, external_links)
        all_candidates.extend(candidates)
        fetched_titles.append(title)

    # Step 3: follow internal wikilinks from already-fetched articles (CHANGE 4).
    # Bounded globally by WIKI_MAX_FOLLOWED_ARTICLES — counts every link we commit
    # to following (whether or not its fetch succeeds) so the crawl truly cannot
    # run away regardless of hit rate.
    for seed_title in fetched_titles:
        if followed_count >= WIKI_MAX_FOLLOWED_ARTICLES:
            break

        linked_titles = _wiki_internal_links(seed_title, max_links=20)
        time.sleep(WIKI_RATE_LIMIT)

        for link_title in linked_titles:
            if followed_count >= WIKI_MAX_FOLLOWED_ARTICLES:
                break

            link_url = f"https://en.wikipedia.org/wiki/{link_title.replace(' ', '_')}"
            if link_url in processed_article_urls:
                continue
            processed_article_urls.add(link_url)
            followed_count += 1

            link_text = _wiki_article_text(link_title)
            if not link_text:
                continue
            time.sleep(WIKI_RATE_LIMIT)

            link_external_links = _wiki_external_links(link_title)
            time.sleep(WIKI_RATE_LIMIT)

            link_candidates = _extract_wiki_candidates(link_text, link_title, seen_urls, link_external_links)
            logger.debug(
                "Wikipedia [linked: %s ← %s]: %d Yishun sections",
                link_title, seed_title, len(link_candidates),
            )
            all_candidates.extend(link_candidates)

    logger.info(
        "Wikipedia: %d candidate sections collected (%d linked article(s) followed)",
        len(all_candidates), followed_count,
    )
    return all_candidates


# ── Link validation helpers ───────────────────────────────────────────────────

WAYBACK_API = "https://archive.org/wayback/available"


def get_wayback_url(url: str) -> Optional[str]:
    """
    Query Wayback Machine availability API.
    Returns the snapshot URL if one exists, else None.
    """
    try:
        resp = httpx.get(
            WAYBACK_API,
            params={"url": url},
            timeout=8.0,
            headers={"User-Agent": _BROWSER_UA},
        )
        resp.raise_for_status()
        snapshot = resp.json().get("archived_snapshots", {}).get("closest", {})
        if snapshot.get("available") and snapshot.get("url"):
            return snapshot["url"]
    except Exception as exc:
        logger.debug("Wayback API error for %s: %s", url[:80], exc)
    return None


def validate_source_urls(urls: list) -> dict:
    """
    HEAD request each URL with 3s timeout. Returns dict keyed by URL:
        { status: 'ok'|'dead'|'paywall'|'redirect', status_code: int, final_url: str }

    Status rules:
      200        → 'ok'
      301/302 same domain → follow, recheck
      301/302 diff domain → 'redirect'
      403/401    → 'paywall'
      404/410    → 'dead'
      timeout    → 'dead'

    Rate limit: 1 request/second.
    """
    results: dict = {}

    for url in urls:
        time.sleep(1.0)
        try:
            resp = httpx.head(
                url,
                follow_redirects=True,
                timeout=3.0,
                headers={"User-Agent": _BROWSER_UA},
            )
            final_url = str(resp.url)
            code = resp.status_code

            if code == 200:
                status = "ok"
            elif code in (401, 403):
                status = "paywall"
            elif code in (404, 410):
                status = "dead"
            else:
                # Check if redirect crossed domains
                try:
                    orig_domain  = httpx.URL(url).host
                    final_domain = httpx.URL(final_url).host
                    status = "ok" if orig_domain == final_domain else "redirect"
                except Exception:
                    status = "dead"

        except (httpx.TimeoutException, httpx.RequestError):
            final_url = url
            code      = 0
            status    = "dead"

        results[url] = {
            "status":      status,
            "status_code": code,
            "final_url":   final_url,
        }
        logger.debug("Link validation [%s] %s → %s", status.upper(), url[:80], code)

    return results


# ── DB helpers ────────────────────────────────────────────────────────────────

def _make_unique_slug(base: str, supabase) -> str:
    """Append numeric suffix until the slug is unique in the incidents table."""
    slug = base
    for i in range(1, 50):
        result = (
            supabase.table("incidents")
            .select("id")
            .eq("slug", slug)
            .limit(1)
            .execute()
        )
        if not result.data:
            return slug
        slug = f"{base}-{i}"
    # Last-resort fallback: append epoch seconds
    return f"{base}-{int(time.time())}"


def _parse_source_date_to_iso(source_date: str) -> Optional[str]:
    """
    Parse a 'YYYY-MM-DD' string into an ISO 8601 UTC timestamp.
    Returns None if source_date is missing, unparseable, or out of range.
    """
    if not source_date:
        return None
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", source_date)
    if not m:
        return None
    try:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        from datetime import date as _date
        _date(y, mo, d)   # validates calendar correctness
        if not (1980 <= y <= 2026):
            return None
        return f"{y:04d}-{mo:02d}-{d:02d}T00:00:00+00:00"
    except ValueError:
        return None


def _build_incident_row(draft: dict, item: dict) -> Optional[dict]:
    """
    Build an incidents table row for direct auto-publish.

    Returns None if the source date is missing/unparseable — the caller
    must downgrade the item to the QUEUE tier instead of auto-publishing
    with a fabricated incident_date.
    """
    raw_date    = item.get("date", "")
    parsed_iso  = _parse_source_date_to_iso(raw_date)

    if parsed_iso is None:
        logger.warning(
            "Backfill: source_date missing/unparseable %r for '%s' "
            "— downgrading to QUEUE tier, operator must set date manually",
            raw_date, draft.get("title", "")[:60],
        )
        return None

    published_at  = parsed_iso
    incident_date = parsed_iso[:10]   # "YYYY-MM-DD"

    # Date sanity (write-time twin of ops.integrity's incident_date_after_source):
    # incident_date is inherited from item["date"]; a corrupt one that post-dates
    # the most recent source reporting the story is a fabrication, not history
    # (a 2018 AsiaOne story that reached the archive dated 2026). Downgrade to the
    # queue like a missing date rather than auto-publishing a wrong date. ISO
    # dates compare chronologically as strings.
    _src_dates = [str(e.get("date"))[:10] for e in (item.get("source_timeline") or []) if e.get("date")]
    _src_dates = [x for x in _src_dates if len(x) == 10 and x[4] == "-" and x[7] == "-"]
    if _src_dates and incident_date > max(_src_dates):
        logger.warning(
            "Backfill: incident_date %s post-dates its most recent source %s for '%s' "
            "— downgrading to QUEUE (item date looks fabricated)",
            incident_date, max(_src_dates), draft.get("title", "")[:60])
        return None

    hype = 1 if item.get("source_type") == "reference" else draft.get("hype_meter", 0)

    # Every corroborating source, de-duplicated and order-preserving. Previously
    # hardcoded to [item["url"]] with corroboration_count=1 and an empty timeline,
    # which discarded multi-source stories entirely (and zeroed the lightning
    # meter, since bolts = corroboration_count - 1).
    source_urls = list(dict.fromkeys(
        u for u in (draft.get("source_urls") or item.get("source_urls") or [item.get("url", "")]) if u
    )) or [item.get("url", "")]

    # Guardrail #2: a signal URL (EDMW/HWZ) is never a quoted source. Unapproved
    # domains are kept and flagged rather than dropped — see
    # classifiers.source_allowlist.
    from classifiers.source_allowlist import check_source_urls
    allow = check_source_urls(source_urls)
    if not allow["kept"]:
        # Everything was signal. Do NOT fall back to the unfiltered list — that
        # would publish the forum URL as a source. Guardrail #1 also forbids
        # publishing with an empty source_urls, so this item cannot auto-publish
        # at all: downgrade to the queue (same contract as a missing date) and
        # let the operator attach an MSM source first.
        logger.warning(
            "Backfill: all source URLs are signal for '%s' — downgrading to QUEUE "
            "tier (guardrail #2: an EDMW/forum URL is never a quoted source)",
            draft.get("title", "")[:60],
        )
        return None
    source_urls = allow["kept"]

    # Coordinates come ONLY from the deterministic OneMap geocoder — never
    # from Stage 2's LLM output (it used to guess the Yishun centre point,
    # which stacked every pin at 1.4295/103.835). No geocode → no pin.
    lat, lon = None, None
    try:
        from classifiers.geocoding import geocode_incident
        coords = geocode_incident(
            draft.get("block_number"), draft.get("area_name"),
            extra_text=draft.get("title"), location_text=draft.get("summary"),
        )
        if coords:
            lat, lon = coords
            logger.debug(
                "Geocoded in build_incident_row: lat=%.5f lon=%.5f", lat, lon,
            )
    except Exception as exc:
        logger.debug("Geocoding in build_incident_row (non-fatal): %s", exc)

    return {
        "incident_date":       incident_date,
        "first_reported_at":   incident_date,
        "title":               draft["title"],
        "summary":             draft["summary"],
        "classification":      draft["classification"],
        "severity":            draft["severity"],
        "block_number":        draft.get("block_number"),
        "area_name":           draft.get("area_name"),
        "latitude":            lat,
        "longitude":           lon,
        "source_urls":         source_urls,
        "hype_meter":          hype,
        "corroboration_count": len(source_urls),
        "edmw_signal_count":   0,
        "seo_title":           draft.get("seo_title", ""),
        "seo_description":     draft.get("seo_description", ""),
        "slug":                draft.get("slug", ""),   # uniquified before insert
        "tags":                draft.get("tags", []),
        "agent_confidence":    draft["confidence"],
        "chaos_contribution":  draft.get("chaos_contribution", 0.0),
        "deaths":              draft.get("deaths"),
        "injuries":            draft.get("injuries"),
        "is_published":        True,
        "published_at":        published_at,     # ← source date, not NOW()
        "is_developing":       False,
        "update_count":        0,
        "source_timeline":     item.get("source_timeline") or [],
        "latest_source_role":  "initial",
        "is_milestone":        False,
    }


# ── Confidence tier dispatcher ────────────────────────────────────────────────

def _dry_run_entry(item: dict, draft: dict, tier: str) -> dict:
    # _merged_from is set on item by _merge_batch_group when items are collapsed.
    return {
        "url":            item.get("url", ""),
        "source_name":    item.get("source_name", "?"),
        "source_type":    item.get("source_type", "msm"),
        "date":           item.get("date", ""),
        "title":          draft.get("title", ""),
        "classification": draft.get("classification", ""),
        "severity":       draft.get("severity", 0),
        "confidence":     draft.get("confidence", 0.0),
        "tier":           tier,
        "merged_from":    item.get("_merged_from", 1),
    }


def _apply_tier(
    item: dict,
    draft: dict,
    confidence: float,
    consolidation,
    dry_run: bool,
    supabase,
    stats: dict,
    link_validation: Optional[dict] = None,
    link_validation_flags: Optional[dict] = None,
) -> None:
    """
    Route each candidate to the correct destination based on consolidation result
    and confidence tier (spec §14c).
    """
    from consolidation.queue_row import build_queue_row

    link_validation_flags = link_validation_flags or {}
    link_validation       = link_validation or {}
    date_missing          = not _parse_source_date_to_iso(item.get("date", ""))

    # Force to queue when ALL source URLs are dead/redirect (conf must be >= 0.85 to auto-pub)
    if link_validation_flags.get("_all_dead") and confidence < 0.85:
        confidence = min(confidence, TIER_QUEUE - 0.01)   # clamp below auto-publish threshold

    is_update = (
        consolidation is not None
        and getattr(consolidation, "action", None) == "update"
    )

    # ── UPDATES — always to War Room, never auto-publish ─────────────────────
    if is_update:
        stats["updates_found"] += 1
        if dry_run:
            stats["queued_for_review"] += 1
            stats["items"].append(_dry_run_entry(item, draft, "UPDATE→queue"))
            return

        try:
            row = build_queue_row(item, draft, consolidation, is_update=True, date_missing=date_missing)
            if link_validation:
                row["raw_content"]["link_validation"] = link_validation
            supabase.table("war_room_queue").insert(row).execute()
            stats["queued_for_review"] += 1
            logger.info(
                "Backfill UPDATE queued — target %s: %s",
                consolidation.matched_incident_id,
                draft.get("title", "")[:60],
            )
        except Exception as exc:
            stats["errors"] += 1
            logger.error("Backfill UPDATE queue insert failed: %s", exc)
        return

    # ── AUTO-PUBLISH tier (confidence >= 0.70) ───────────────────────────────
    if confidence >= TIER_AUTO_PUBLISH:
        date_ok = _parse_source_date_to_iso(item.get("date", "")) is not None

        if dry_run:
            if date_ok:
                stats["auto_published"] += 1
                stats["items"].append(_dry_run_entry(item, draft, "AUTO-PUBLISH"))
            else:
                stats["queued_for_review"] += 1
                stats["items"].append(_dry_run_entry(item, draft, "QUEUE (date fallback)"))
            return

        incident_row = _build_incident_row(draft, item)

        if incident_row is None:
            # Unparseable/missing date — downgrade to QUEUE for operator review.
            # build_queue_row sets raw_content._date_fallback = True automatically.
            try:
                row = build_queue_row(item, draft, consolidation, date_missing=date_missing)
                if link_validation:
                    row["raw_content"]["link_validation"] = link_validation
                supabase.table("war_room_queue").insert(row).execute()
                stats["queued_for_review"] += 1
                logger.info(
                    "Backfill date-fallback QUEUE [conf=%.2f]: %s",
                    confidence,
                    draft.get("title", "")[:70],
                )
            except Exception as exc:
                stats["errors"] += 1
                logger.error("Backfill date-fallback queue insert failed: %s", exc)
            return

        incident_row["slug"] = _make_unique_slug(incident_row["slug"], supabase)

        try:
            result = (
                supabase.table("incidents")
                .insert(incident_row)
                .select("id")
                .execute()
            )
            new_id = (result.data or [{}])[0].get("id")
            stats["auto_published"] += 1
            logger.info(
                "Backfill AUTO-PUBLISH [%s sev=%d conf=%.2f]: %s",
                draft["classification"].upper(),
                draft["severity"],
                confidence,
                draft.get("title", "")[:70],
            )

            # Post-publish hooks (non-fatal on failure)
            if new_id:
                try:
                    from classifiers.pattern_detection import run as pattern_run
                    pattern_run(supabase_client=supabase)
                except Exception as exc:
                    logger.debug("Pattern detection post-backfill (non-fatal): %s", exc)
                try:
                    from orchestrator.herald_agent import check_milestones
                    check_milestones(
                        draft           = draft,
                        queue_id        = new_id,
                        source_url      = item["url"],
                        incident_title  = draft.get("title", ""),
                        supabase_client = supabase,
                    )
                except Exception as exc:
                    logger.debug("Herald agent post-backfill (non-fatal): %s", exc)

        except Exception as exc:
            stats["errors"] += 1
            logger.error("Backfill auto-publish insert failed: %s", exc)
        return

    # ── QUEUE FOR REVIEW (confidence 0.50–0.69) ──────────────────────────────
    if confidence >= TIER_QUEUE:
        if dry_run:
            stats["queued_for_review"] += 1
            stats["items"].append(_dry_run_entry(item, draft, "QUEUE"))
            return

        try:
            row = build_queue_row(item, draft, consolidation, date_missing=date_missing)
            if link_validation:
                row["raw_content"]["link_validation"] = link_validation
            supabase.table("war_room_queue").insert(row).execute()
            stats["queued_for_review"] += 1
            logger.info(
                "Backfill queued for review [conf=%.2f]: %s",
                confidence,
                draft.get("title", "")[:70],
            )
        except Exception as exc:
            stats["errors"] += 1
            logger.error("Backfill queue insert failed: %s", exc)
        return

    # ── SILENT REJECT (confidence < 0.50) ────────────────────────────────────
    stats["rejected"] += 1
    if dry_run:
        stats["items"].append(_dry_run_entry(item, draft, "REJECT"))
    logger.debug(
        "Backfill REJECT [conf=%.2f]: %s",
        confidence,
        draft.get("title", "")[:70],
    )


# ── Intra-batch deduplication ────────────────────────────────────────────────
#
# Strategy: group by (date window <=7 days) AND (location overlap: same
# block_number OR area_name from Stage 2). One Haiku call per group — not
# pairwise — returns {is_same, primary_index, role_assignments[]}.

_BATCH_GROUP_JUDGE_PROMPT = """\
You are a deduplication agent for a Yishun, Singapore incident archive.

Below are N news articles that occurred around the same time and location.
Determine whether they ALL describe the same real-world incident.

Definition: same incident = same physical event. Multiple articles covering
the initial report, the arrest, the charges, and the sentencing of the SAME
event are all the "same incident". Two unrelated crimes near each other are
NOT the same incident.

Return JSON only -- no markdown, no explanation outside the object:
{
  "is_same": boolean,
  "primary_index": integer (0-based index of the best article -- prefer earliest or most detailed),
  "role_assignments": array of strings, one entry per article in the order given
}

Role values:
  "initial"   -- first report of the incident
  "update"    -- new development (arrest after attack, charges after arrest)
  "follow_up" -- tangential coverage (community reaction, background, later mention)

If is_same is false, still return primary_index=0 and fill role_assignments
with "initial" for each article.
"""


def _batch_keywords(draft: dict) -> set:
    """
    Significant keywords from title+summary.
    Used as fallback location signal when Stage 2 extracted no block/area.
    """
    STOP = {
        "yishun", "singapore", "the", "and", "for", "that", "this", "with",
        "from", "after", "block", "flat", "home", "house", "found", "told",
        "said", "have", "been", "were", "they", "their", "will", "also",
        "police", "court", "charged", "jailed", "arrested", "man", "woman",
        "year", "years", "into", "when", "then", "more", "dead", "body",
    }
    text = (draft.get("title", "") + " " + draft.get("summary", "")).lower()
    words = re.findall(r"[a-z]{4,}", text)
    return {w for w in words if w not in STOP}


def _same_date_window(a: dict, b: dict, days: int = 7) -> bool:
    """Return True if two batch entries have dates within `days` of each other."""
    from datetime import date as _date
    date_a = a["item"].get("date", "")
    date_b = b["item"].get("date", "")
    if not date_a or not date_b:
        return False
    try:
        return abs((_date.fromisoformat(date_a) - _date.fromisoformat(date_b)).days) <= days
    except ValueError:
        return False


def _same_location(a: dict, b: dict) -> bool:
    """
    Return True if two entries share the same location signal.

    Priority:
      1. Same block_number  (e.g. "Block 120A") -- strong
      2. Same area_name     (e.g. "Yishun Ave 4") -- medium
      3. Title keyword overlap >= 3 when BOTH have no location data -- fallback
    """
    block_a = (a["draft"].get("block_number") or "").strip().lower()
    block_b = (b["draft"].get("block_number") or "").strip().lower()
    if block_a and block_b and block_a == block_b:
        return True

    area_a = (a["draft"].get("area_name") or "").strip().lower()
    area_b = (b["draft"].get("area_name") or "").strip().lower()
    if area_a and area_b and area_a == area_b:
        return True

    # Fallback: both entries lack location data
    if not (block_a or area_a) and not (block_b or area_b):
        return len(_batch_keywords(a["draft"]) & _batch_keywords(b["draft"])) >= 3

    return False


def _group_by_date_and_location(processed: list) -> list:
    """
    Cluster batch entries into groups where every pair shares a <=7-day date
    window AND the same location signal. Uses union-find so transitive matches
    (A~B, B~C => A,B,C same group) are captured correctly.

    Returns a list of groups, each a list of indices into `processed`.
    Single-item groups are included (they pass through unchanged).
    """
    n = len(processed)
    parent = list(range(n))

    def _find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def _union(x: int, y: int) -> None:
        parent[_find(x)] = _find(y)

    for i in range(n):
        for j in range(i + 1, n):
            if _same_date_window(processed[i], processed[j]) and \
               _same_location(processed[i], processed[j]):
                _union(i, j)

    groups: dict = {}
    for idx in range(n):
        groups.setdefault(_find(idx), []).append(idx)
    return list(groups.values())


def _judge_batch_group(client, group: list) -> dict:
    """
    Ask Claude Haiku whether all items in a location+date group are the
    same real-world incident. One API call per group regardless of group size.

    Returns:
        {
            "is_same":          bool,
            "primary_index":    int,         # 0-based index into group
            "role_assignments": list[str],   # one per entry
        }
    """
    import json as _json

    article_blocks = []
    for i, entry in enumerate(group):
        article_blocks.append(
            f"[{i}]\n"
            f"Date:     {entry['item'].get('date', 'unknown')}\n"
            f"Title:    {entry['draft'].get('title', entry['item'].get('title', ''))}\n"
            f"Location: block={entry['draft'].get('block_number') or 'unknown'}"
            f"  area={entry['draft'].get('area_name') or 'unknown'}\n"
            f"Summary:  {entry['draft'].get('summary', entry['item'].get('content', ''))[:400]}"
        )
    user_msg = "\n\n".join(article_blocks)

    import anthropic as _anthropic
    response = client.messages.create(
        model       = "claude-haiku-4-5-20251001",
        max_tokens  = 300,
        temperature = 0.0,
        system      = _BATCH_GROUP_JUDGE_PROMPT,
        messages    = [{"role": "user", "content": user_msg}],
    )

    raw = response.content[0].text.strip()
    raw = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()

    try:
        result = _json.loads(raw)
    except _json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", raw)
        result = _json.loads(match.group()) if match else {}

    is_same       = bool(result.get("is_same", False))
    primary_index = max(0, min(int(result.get("primary_index", 0)), len(group) - 1))

    valid_roles   = {"initial", "update", "follow_up"}
    raw_roles     = result.get("role_assignments", [])
    roles         = [r if r in valid_roles else "update" for r in raw_roles]
    # Pad/truncate to exactly len(group)
    while len(roles) < len(group):
        roles.append("update")
    roles = roles[:len(group)]

    return {"is_same": is_same, "primary_index": primary_index, "role_assignments": roles}


def _merge_batch_group(group: list, role_assignments: list) -> dict:
    """
    Merge multiple same-incident batch entries into a single card.

    - Primary: highest-confidence entry
    - source_timeline: all entries chronologically, each with source_role tag
    - source_urls: combined unique non-Wikipedia URLs
    - hype_meter: recomputed from combined URLs
    - update_count: len(group) - 1
    - incident_date: earliest article date
    """
    from classifiers.corroboration import compute_hype_meter

    # Sort by date for timeline; keep original index for role lookup
    indexed_by_date = sorted(
        enumerate(group),
        key=lambda x: x[1]["item"].get("date", "9999-99-99"),
    )

    # Primary = highest confidence (best quality draft to display)
    primary_idx = max(range(len(group)), key=lambda i: group[i]["confidence"])
    primary     = group[primary_idx]
    base_draft  = dict(primary["draft"])   # shallow copy

    # Source timeline tagged with Haiku-assigned roles
    source_timeline = [
        {
            "date":        entry["item"].get("date", ""),
            "headline":    entry["item"].get("title", ""),
            "source_url":  entry["item"].get("url", ""),
            "source_name": entry["item"].get("source_name", ""),
            "source_role": role_assignments[orig_idx] if orig_idx < len(role_assignments) else "update",
        }
        for orig_idx, entry in indexed_by_date
    ]

    # Combined source_urls — Wikipedia reference URLs excluded (spec §13.2)
    all_urls: list = []
    seen_u: set = set()
    for _, entry in indexed_by_date:
        u = entry["item"].get("url", "")
        if u and u not in seen_u and entry["item"].get("source_type") != "reference":
            seen_u.add(u)
            all_urls.append(u)

    hype = compute_hype_meter(all_urls) if all_urls else base_draft.get("hype_meter", 0)

    base_draft["source_urls"]     = all_urls or [primary["item"].get("url", "")]
    base_draft["hype_meter"]      = hype
    base_draft["source_timeline"] = source_timeline
    base_draft["update_count"]    = len(group) - 1

    earliest_item = indexed_by_date[0][1]["item"]
    merged_item = dict(primary["item"])
    merged_item["date"]         = earliest_item.get("date", primary["item"].get("date", ""))
    merged_item["_merged_from"] = len(group)

    return {
        "item":       merged_item,
        "draft":      base_draft,
        "confidence": primary["confidence"],
        "is_wiki":    primary["is_wiki"],
    }


def _shared_source_url(group_entries: list) -> Optional[str]:
    """
    Return a source URL shared by 2+ items in the group, or None.

    Catches same-source duplicates (multiple Wikipedia paragraphs from one
    article, or the same Google News article matched by different keyword
    searches) that would otherwise be left to a Haiku same-incident judgment
    that can be confused by differently-framed text from one source.
    """
    seen: set = set()
    for entry in group_entries:
        url = entry["item"].get("url", "")
        if not url:
            continue
        if url in seen:
            return url
        seen.add(url)
    return None


def _dedup_batch(processed: list, stats: dict) -> list:
    """
    Intra-batch duplicate detection: group by location+date, then judge per group.

    Algorithm:
      1. Group entries by (date <=7 days) AND (same block_number / area_name).
         Title-keyword overlap >= 3 used as fallback when Stage 2 found no location.
      2. Within each group, force-merge items that share an identical source_url
         (e.g. multiple Wikipedia paragraphs from the same article) -- skips the
         Haiku call entirely for these.
      3. For each remaining group of 2+: ONE Haiku call presenting all articles
         at once. Returns {is_same, primary_index, role_assignments}.
      4. If is_same=True: merge into one card with role-tagged source_timeline.

    Haiku API calls = number of multi-item groups not resolved by step 2.
    """
    import os as _os
    import anthropic as _anthropic

    n = len(processed)
    if n <= 1:
        return processed

    # Step 1: structural grouping (no API cost)
    all_groups   = _group_by_date_and_location(processed)
    multi_groups = [g for g in all_groups if len(g) > 1]

    if not multi_groups:
        logger.debug("Intra-batch dedup: %d items, no location+date groups -- skip", n)
        return processed

    logger.info(
        "Intra-batch dedup: %d item(s), %d group(s) of 2+ to judge with Haiku",
        n, len(multi_groups),
    )

    # Step 2: Haiku client
    api_key = _os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("Intra-batch dedup: ANTHROPIC_API_KEY not set -- skipping")
        return processed

    client = _anthropic.Anthropic(api_key=api_key)

    result: list = []

    # Emit singletons directly
    for g in all_groups:
        if len(g) == 1:
            result.append(processed[g[0]])

    # Step 3: force-merge same-source-URL groups, then judge the rest with Haiku
    for group_indices in multi_groups:
        group_entries = [processed[i] for i in group_indices]

        shared_url = _shared_source_url(group_entries)
        if shared_url:
            n_articles = len(group_entries)
            role_assignments = ["initial"] + ["update"] * (n_articles - 1)
            merged = _merge_batch_group(group_entries, role_assignments)
            stats["batch_merges"] += n_articles - 1
            logger.info("Force-merged %d items sharing source_url: %s", n_articles, shared_url)
            result.append(merged)
            continue

        try:
            judgment = _judge_batch_group(client, group_entries)
        except Exception as exc:
            logger.warning(
                "Batch group judge failed for indices %s: %s -- keeping separate",
                group_indices, exc,
            )
            result.extend(group_entries)
            continue

        if not judgment["is_same"]:
            logger.debug(
                "Intra-batch group %s: NOT same incident -- keeping separate",
                group_indices,
            )
            result.extend(group_entries)
            continue

        merged     = _merge_batch_group(group_entries, judgment["role_assignments"])
        n_articles = len(group_entries)
        stats["batch_merges"] += n_articles - 1

        logger.info(
            "Intra-batch MERGED %d articles (roles: %s) -> '%s'",
            n_articles,
            ", ".join(judgment["role_assignments"]),
            merged["draft"].get("title", "")[:65],
        )
        result.append(merged)

    return result


# ── War Room summary notification ─────────────────────────────────────────────

def _write_summary_notification(stats: dict, supabase) -> None:
    """
    Insert a summary sentinel into war_room_queue so the War Room can render
    the BACKFILL COMPLETE banner. Silently skips on DB failure.
    """
    try:
        supabase.table("war_room_queue").insert({
            "raw_content": {
                "notification_type": "backfill_summary",
                "run_at":            stats["run_at"],
                "scraped":           stats["scraped"],
                "auto_published":    stats["auto_published"],
                "queued_for_review": stats["queued_for_review"],
                "updates_found":     stats["updates_found"],
                "rejected":          stats["rejected"],
                "errors":            stats["errors"],
            },
            # Required non-null columns; values are sentinel strings
            "source_url":  f"_backfill_summary_{int(time.time())}",
            "source_type": "msm",
            "status":      "pending",
        }).execute()
        logger.debug("Backfill summary notification written")
    except Exception as exc:
        logger.warning("Could not write backfill summary notification: %s", exc)


# ── Shared candidate pipeline ────────────────────────────────────────────────

def process_candidates(
    all_candidates: list,
    cap: int,
    dry_run: bool,
    supabase,
    stats: dict,
    budget: Stage1DailyQuota,
) -> None:
    """
    Run Stage 1 -> Stage 2 -> intra-batch dedup -> consolidation -> tier routing
    for a list of raw candidate dicts (the standard backfill candidate format:
    {title, content, url, source_name, source_type, date}).

    Mutates `stats` in place and writes to `supabase` (when not dry_run) via
    _apply_tier / _write_summary_notification (called separately by the caller).

    Shared by run_backfill() (Google News RSS / Wikipedia candidates) and
    run_historical_search() (GDELT / Google web / Yahoo candidates).
    """
    from filters.stage1_filter import filter_content
    from filters.stage2_writer import write_stage2
    from classifiers.corroboration import check_duplicate
    from consolidation.check import check as consolidation_check

    # ── Phase 2a: Stage 1 → Stage 2 → collect ────────────────────────────────
    # Do NOT route to tiers yet — collect everything first so the intra-batch
    # deduplication pass (Phase 2b) can merge same-incident items.

    processed_batch: list = []   # each: {item, draft, confidence, is_wiki}
    s2_count = 0

    for item in all_candidates:
        if s2_count >= cap:
            stats["capped"] = True
            break

        url     = item.get("url", "")
        is_wiki = item.get("source_type") == "reference"

        # ── Cheap local pre-filter (no API cost) ─────────────────────────────
        if not is_wiki and _prefilter_is_noise(item):
            stats["prefiltered"] += 1
            if dry_run:
                stats["items"].append(_dry_run_entry(item, {
                    "title":          item.get("title", ""),
                    "classification": "",
                    "severity":       0,
                    "confidence":     0.0,
                }, "PRE-FILTER"))
            continue

        # Duplicate check against DB (skip in dry_run)
        if not dry_run and check_duplicate(url, client=supabase):
            stats["duplicates_skipped"] += 1
            continue

        # ── Stage 1 ──────────────────────────────────────────────────────────
        # Wikipedia (reference) items bypass Stage 1 entirely — they're already
        # spec-floored to WIKI_CONFIDENCE_FLOOR and never read like scraped noise.
        # Bypassed items must NOT touch the Stage 1 budget (CHANGE 5/6).
        if is_wiki:
            logger.info("Wikipedia item bypassing Stage 1: %s", item.get("title", "")[:60])
        else:
            try:
                s1 = filter_content(item)
            except Stage1HaltError as exc:
                # Non-retryable (daily quota gone, or billing blocked) — retrying
                # every remaining candidate would just hammer the same wall.
                budget.mark_rpd_exhausted()
                stats["errors"] += 1
                if len(stats["error_details"]) < 100:
                    stats["error_details"].append({
                        "phase": "stage1", "url": url[:120],
                        "error": f"{type(exc).__name__}: {exc}",
                    })
                logger.warning("Stage 1 halted — stopping loop cleanly: %s", exc)
                break
            except Exception as exc:
                stats["errors"] += 1
                if len(stats["error_details"]) < 100:
                    stats["error_details"].append({
                        "phase": "stage1", "url": url[:120],
                        "error": f"{type(exc).__name__}: {exc}",
                    })
                logger.error("Stage 1 error [%s]: %s", url[:80], exc)
                continue

            usage = s1.get("usage") or {}
            budget.record(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
            if budget.should_halt():
                logger.warning("Stage 1 daily request budget halt — stopping candidate processing loop cleanly.")
                break

            if not s1["passes"]:
                stats["stage1_rejected"] += 1
                continue

            stats["stage1_passed"] += 1

        # ── Stage 2 ──────────────────────────────────────────────────────────
        try:
            # Preserve the caller's aggregated source list. seed_backfill groups a
            # story's URLs into ONE candidate (item["source_urls"] = every fetched
            # source); collapsing to [url] here silently dropped every corroborating
            # source, so published incidents shipped with 1 source_url and
            # corroboration_count=1 no matter how many were fetched.
            aggregated = list(dict.fromkeys(u for u in (item.get("source_urls") or []) if u))
            stage2_input = {
                **item,
                "source_urls":       [] if is_wiki else (aggregated or [url]),
                "edmw_signal_count": 0,
            }
            draft = write_stage2(stage2_input)
            stats["stage2_processed"] += 1
        except Exception as exc:
            stats["stage2_errors"] += 1
            if len(stats["error_details"]) < 100:
                stats["error_details"].append({
                    "phase": "stage2", "url": url[:120],
                    "error": f"{type(exc).__name__}: {exc}",
                })
            logger.error("Stage 2 error [%s]: %s", url[:80], exc)
            time.sleep(STAGE2_RATE_LIMIT)
            continue

        # Wikipedia treatment overrides (spec §14c)
        confidence = draft["confidence"]
        if is_wiki:
            confidence           = max(confidence, WIKI_CONFIDENCE_FLOOR)
            draft["confidence"]  = confidence
            draft["hype_meter"]  = 1

        # ── Link validation ───────────────────────────────────────────────────
        source_urls_to_check = [u for u in (draft.get("source_urls") or [item.get("url", "")]) if u]
        link_validation: dict = {}
        link_validation_flags: dict = {}

        if source_urls_to_check and not is_wiki:
            try:
                link_validation = validate_source_urls(source_urls_to_check)
            except Exception as exc:
                logger.warning("Link validation failed (non-fatal): %s", exc)

            all_statuses = [v["status"] for v in link_validation.values()]

            # Replace dead URLs with Wayback snapshots where possible
            for u, info in link_validation.items():
                if info["status"] == "dead":
                    wb = get_wayback_url(u)
                    if wb:
                        link_validation[u]["wayback_url"] = wb
                        link_validation_flags[u] = "wayback_substituted"
                        logger.info("Wayback substitution: %s → %s", u[:60], wb[:60])

            # Confidence override: all dead/redirect AND conf < 0.85 → force to queue
            live_statuses = {"ok", "paywall"}   # paywall = article exists, just gated
            has_live = any(s in live_statuses for s in all_statuses)
            if not has_live and all_statuses and confidence < 0.85:
                link_validation_flags["_all_dead"] = True
                logger.info(
                    "Link validation: ALL urls dead/redirect for '%s' — forcing to queue",
                    draft.get("title", "")[:70],
                )

        processed_batch.append({
            "item":             item,
            "draft":            draft,
            "confidence":       confidence,
            "is_wiki":          is_wiki,
            "link_validation":  link_validation,
            "link_validation_flags": link_validation_flags,
        })
        s2_count += 1
        time.sleep(STAGE2_RATE_LIMIT)

    # ── Phase 2b: Intra-batch deduplication ───────────────────────────────────
    # Find same-incident items within this batch and merge them before routing.
    # The existing consolidation agent only checks against published incidents;
    # this pass catches duplicates that arrived together in the same scrape window.

    logger.info(
        "Backfill — intra-batch dedup: %d S2 items to check", len(processed_batch)
    )
    deduplicated = _dedup_batch(processed_batch, stats)
    logger.info(
        "Backfill — dedup complete: %d items → %d (merged %d duplicate(s))",
        len(processed_batch), len(deduplicated),
        len(processed_batch) - len(deduplicated),
    )

    # ── Phase 2c: Consolidation check (vs published) + tier routing ───────────

    for entry in deduplicated:
        item                   = entry["item"]
        draft                  = entry["draft"]
        confidence             = entry["confidence"]
        link_validation        = entry.get("link_validation", {})
        link_validation_flags  = entry.get("link_validation_flags", {})

        consolidation = None
        if not dry_run:
            try:
                consolidation = consolidation_check(draft, supabase_client=supabase)
                if consolidation.action == "skip":
                    stats["duplicates_skipped"] += 1
                    logger.debug("Consolidation SKIP: %s", draft.get("title", "")[:60])
                    continue
            except Exception as exc:
                logger.warning("Consolidation check failed (non-fatal): %s", exc)

        _apply_tier(
            item                  = item,
            draft                 = draft,
            confidence            = confidence,
            consolidation         = consolidation,
            dry_run               = dry_run,
            supabase              = supabase,
            stats                 = stats,
            link_validation       = link_validation,
            link_validation_flags = link_validation_flags,
        )


# ── Main runner ──────────────────────────────────────────────────────────────

def run_backfill(
    start_year: int = 2015,
    end_year: int = 2026,
    include_wikipedia: bool = True,
    dry_run: bool = False,
    limit: int = MAX_ITEMS_PER_RUN,
    wikipedia_only: bool = False,
    force_deprecated: bool = False,
) -> dict:
    """
    Run the historical backfill.

    Args:
        start_year:        First year for Google News scrape (inclusive).
        end_year:          Last year for Google News scrape (inclusive, max 2026).
        include_wikipedia: Also scrape Wikipedia for 1980–2014 incidents.
        dry_run:           Run Stage 1/2/consolidation but write nothing to Supabase.
        wikipedia_only:    Skip Google News entirely and run only the Wikipedia
                           phase, regardless of start_year/end_year/include_wikipedia.
        force_deprecated:  Required to run the "recent" Google News RSS path
                           (wikipedia_only=False). DEPRECATED — see
                           INGESTION_DESIGN.md §10b; this path is being replaced
                           by ingestion/orchestrator.py::run_ingestion_pass().
                           The wikipedia_only path is unaffected.

    Returns:
        Stats dict with counts for all outcomes.
    """
    from classifiers.corroboration import get_supabase_client

    budget = Stage1DailyQuota()

    end_year  = min(end_year, 2026)

    run_at = datetime.now(timezone.utc).isoformat()
    stats: dict = {
        "run_at":             run_at,
        "dry_run":            dry_run,
        "scraped":            0,
        "stage1_passed":      0,
        "stage1_rejected":    0,
        "stage2_processed":   0,
        "stage2_errors":      0,
        "duplicates_skipped": 0,
        "prefiltered":        0,    # cheap local rejects before Stage 1 call
        "batch_merges":       0,    # same-incident items collapsed within the batch
        "auto_published":     0,
        "queued_for_review":  0,
        "updates_found":      0,
        "rejected":           0,
        "errors":             0,
        "capped":             False,
        "items":              [],   # populated in dry_run for reporting
        "error_details":      [],   # list of {phase, url, error} — capped at 100
    }

    # ── Deprecation guard ─────────────────────────────────────────────────────
    # The "recent" Google News RSS path (wikipedia_only=False) is deprecated by
    # INGESTION_DESIGN.md §10b in favour of ingestion/orchestrator.py::run_ingestion_pass().
    # The wikipedia_only path remains valid for historical backfill.
    if not wikipedia_only and not force_deprecated:
        logger.warning(
            "run_backfill(): the 'recent' Google News RSS path (wikipedia_only=False) "
            "is DEPRECATED (INGESTION_DESIGN.md §10b) and will be replaced by "
            "ingestion/orchestrator.py::run_ingestion_pass(). Refusing to run. "
            "Pass force_deprecated=True (CLI: --force-deprecated) to run it anyway, "
            "or use wikipedia_only=True / --historical-search for supported paths."
        )
        stats["errors"] = 1
        stats["error_details"].append({
            "phase": "deprecation_guard",
            "url":   "",
            "error": (
                "run_backfill() 'recent' path is deprecated — pass "
                "force_deprecated=True or use wikipedia_only=True / --historical-search"
            ),
        })
        return stats

    # ── Supabase client (not needed in dry_run) ──────────────────────────────
    supabase = None
    if not dry_run:
        try:
            supabase = get_supabase_client()
        except EnvironmentError as exc:
            logger.error("Supabase not configured — cannot run live backfill: %s", exc)
            stats["errors"] += 1
            return stats

    cap = max(1, int(limit))   # honour --limit / API limit parameter

    # ── Phase 1: Collect all raw candidates ──────────────────────────────────

    seen_urls: set = set()
    all_candidates: list = []

    if not wikipedia_only:
        logger.info(
            "Backfill — collecting candidates: Google News %d–%d, %d keywords",
            start_year, end_year, len(BACKFILL_KEYWORDS),
        )

        for year in range(start_year, end_year + 1):
            for keyword in BACKFILL_KEYWORDS:
                items = _scrape_gnews_year(keyword, year, seen_urls)
                all_candidates.extend(items)
                stats["scraped"] += len(items)

                if stats["scraped"] >= cap:
                    stats["capped"] = True
                    logger.info(
                        "Backfill: raw cap (%d) reached at year=%d keyword=%s",
                        cap, year, keyword,
                    )
                    break
                time.sleep(GNEWS_RATE_LIMIT)

            if stats["capped"]:
                break

    if (include_wikipedia or wikipedia_only) and not stats["capped"]:
        logger.info("Backfill — collecting Wikipedia candidates (1980–2014)")
        wiki_items = _scrape_wikipedia(seen_urls)
        all_candidates.extend(wiki_items)
        stats["scraped"] += len(wiki_items)

    logger.info(
        "Backfill — %d raw candidates collected (capped=%s)",
        len(all_candidates), stats["capped"],
    )

    # ── Phase 2: Stage 1 → Stage 2 → dedup → consolidation → tier routing ────

    process_candidates(all_candidates, cap, dry_run, supabase, stats, budget)

    # ── Write War Room summary notification ───────────────────────────────────
    if not dry_run and supabase is not None:
        _write_summary_notification(stats, supabase)

    logger.info(
        "Backfill done: scraped=%d s1_pass=%d s2_ok=%d dupes=%d "
        "auto_pub=%d queued=%d updates=%d rejected=%d errors=%d",
        stats["scraped"], stats["stage1_passed"], stats["stage2_processed"],
        stats["duplicates_skipped"], stats["auto_published"],
        stats["queued_for_review"], stats["updates_found"],
        stats["rejected"], stats["errors"],
    )

    if wikipedia_only:
        year_range = f"{WIKI_YEAR_MIN}-{WIKI_YEAR_MAX} (wikipedia-only)"
    elif include_wikipedia:
        year_range = f"{start_year}-{end_year} + wikipedia {WIKI_YEAR_MIN}-{WIKI_YEAR_MAX}"
    else:
        year_range = f"{start_year}-{end_year}"

    budget.write_log(extra={"year_range": year_range, "limit": limit})

    return stats


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # httpx logs every redirect at INFO — suppress to WARNING to keep output readable
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    # ── Hero incident link validation test ────────────────────────────────────
    if "--validate-heroes" in sys.argv:
        logging.basicConfig(level=logging.WARNING,
                            format="%(asctime)s %(levelname)s: %(message)s")
        from classifiers.corroboration import get_supabase_client

        HERO_SLUGS = [
            "yishun-cat-killings-serial-mutilation-2015-2016",
            "yishun-triple-murder-wang-zhijian-block-349-2008",
            "yishun-taxi-driver-murders-1992",
            "yishun-infant-murder-mohamed-aliff-2019",
            "kurt-tay-void-deck-fight-yishun-2022",
            "kurt-tay-intimate-video-case-2023-2026",
            "yishun-noise-murder-koh-ah-hwee-block-323-2025",
            "japanese-youtuber-visits-yishun-2023",
        ]
        try:
            sb = get_supabase_client()
            rows = sb.table("incidents").select("slug,source_urls").in_("slug", HERO_SLUGS).execute()
            print(f"\n{'=' * 68}")
            print("HERO INCIDENT LINK VALIDATION")
            print(f"{'=' * 68}")
            for row in (rows.data or []):
                urls = row.get("source_urls") or []
                print(f"\n• {row['slug']}")
                if not urls:
                    print("  (no source_urls)")
                    continue
                results = validate_source_urls(urls)
                for url, info in results.items():
                    badge = {"ok": "[LIVE]", "paywall": "[PAYWALL]", "dead": "[DEAD]", "redirect": "[REDIRECT]"}.get(info["status"], "[?]")
                    wb = get_wayback_url(url) if info["status"] == "dead" else None
                    wb_line = f"\n    -> Wayback: {wb[:80]}" if wb else ""
                    print(f"  {badge}  [{info['status_code']}]  {url[:80]}{wb_line}")
            print(f"\n{'=' * 68}\n")
        except Exception as exc:
            print(f"Error: {exc}")
        sys.exit(0)

    dry_run          = "--dry-run" in sys.argv
    no_wiki          = "--no-wikipedia" in sys.argv
    wiki_only        = "--wikipedia-only" in sys.argv
    historical       = "--historical-search" in sys.argv
    force_deprecated = "--force-deprecated" in sys.argv

    if wiki_only:
        logger.info("Running Wikipedia-only mode (%d–%d)", WIKI_YEAR_MIN, WIKI_YEAR_MAX)
    if historical:
        logger.info("Running historical search mode (Google web + CNA Google site search)")

    start_year = 2015
    end_year   = 2026
    limit      = MAX_ITEMS_PER_RUN

    for arg in sys.argv:
        if arg.startswith("--start-year="):
            start_year = int(arg.split("=")[1])
        if arg.startswith("--end-year="):
            end_year = int(arg.split("=")[1])
        if arg.startswith("--limit="):
            limit = int(arg.split("=")[1])
        if arg.startswith("--year="):
            # --year=2015 is shorthand for --start-year=2015 --end-year=2015
            start_year = end_year = int(arg.split("=")[1])

    if wiki_only:
        gnews_label = "skipped (--wikipedia-only)"
    elif historical:
        gnews_label = f"historical search ({start_year}–{end_year})"
    else:
        gnews_label = "recent RSS (rolling window)"

    print(f"\n{'=' * 68}")
    print(f"Yishun Again — Historical Backfill {'DRY RUN' if dry_run else 'LIVE RUN'}")
    print(f"  Google News:  {gnews_label}")
    print(f"  Wikipedia:    {f'yes ({WIKI_YEAR_MIN}–{WIKI_YEAR_MAX})' if (not no_wiki or wiki_only) else 'no'}")
    print(f"  Limit:        {limit} items")
    print(f"  Auto-publish: confidence >= {TIER_AUTO_PUBLISH}")
    print(f"  Queue:        confidence {TIER_QUEUE}–{TIER_AUTO_PUBLISH - 0.01:.2f}")
    print(f"  Reject:       confidence < {TIER_QUEUE}")
    if dry_run:
        print("  NOTE: DRY RUN — no writes to Supabase")
    print(f"{'=' * 68}\n")

    if historical:
        from scrapers.historical_search_agent import run_historical_search
        stats = run_historical_search(
            start_year         = start_year,
            end_year           = end_year,
            include_wikipedia  = not no_wiki,
            dry_run            = dry_run,
            limit              = limit,
        )
    else:
        stats = run_backfill(
            start_year         = start_year,
            end_year           = end_year,
            include_wikipedia  = not no_wiki,
            dry_run            = dry_run,
            limit              = limit,
            wikipedia_only     = wiki_only,
            force_deprecated   = force_deprecated,
        )

    print(f"\n{'=' * 68}")
    print("BACKFILL SUMMARY")
    print(f"{'=' * 68}")
    print(f"  Run at:             {stats['run_at']}")
    print(f"  Dry run:            {stats['dry_run']}")
    print(f"  Scraped:            {stats['scraped']}")
    print(f"  Pre-filtered:       {stats['prefiltered']}  #cheap noise drop (no API call)")
    print(f"  Stage 1 passed:     {stats['stage1_passed']}")
    print(f"  Stage 1 rejected:   {stats['stage1_rejected']}")
    print(f"  Stage 2 processed:  {stats['stage2_processed']}")
    print(f"  Stage 2 errors:     {stats['stage2_errors']}")
    print(f"  Batch merges:       {stats['batch_merges']}  #same-incident items collapsed")
    print(f"  Duplicates skipped: {stats['duplicates_skipped']}")
    print(f"  Auto-published:     {stats['auto_published']}")
    print(f"  Queued for review:  {stats['queued_for_review']}")
    print(f"  Updates found:      {stats['updates_found']}")
    print(f"  Rejected:           {stats['rejected']}")
    print(f"  Errors:             {stats['errors']}")
    print(f"  Capped at {limit:<6}    {stats['capped']}")

    if stats.get("error_details"):
        print(f"\n{'-' * 68}")
        print(f"ERROR BREAKDOWN ({len(stats['error_details'])} captured, cap=100):")
        print(f"{'-' * 68}")
        # Summarise by type
        from collections import Counter
        type_counts: Counter = Counter(e["phase"] for e in stats["error_details"])
        for phase, count in type_counts.most_common():
            print(f"  {phase:<10} {count} error(s)")
        print()
        # Show first 20 individual errors
        for i, err in enumerate(stats["error_details"][:20], 1):
            print(f"  [{i:>2}] [{err['phase']}] {err['error'][:90]}")
            print(f"        url: {err['url'][:80]}")
        if len(stats["error_details"]) > 20:
            print(f"  ... {len(stats['error_details']) - 20} more errors not shown.")

    if dry_run and stats["items"]:
        print(f"\n{'-' * 68}")
        print(f"ITEMS SAMPLED ({len(stats['items'])} shown):")
        print(f"{'-' * 68}")
        for i, it in enumerate(stats["items"][:30], 1):
            merged_tag = f"  [merged {it['merged_from']} articles]" if it.get("merged_from", 1) > 1 else ""
            print(
                f"\n  [{i:>2}] [{it['tier']:<18}] "
                f"{it['classification'].upper() if it['classification'] else '?':6} "
                f"sev={it['severity']}  conf={it['confidence']:.2f}"
                f"{merged_tag}"
            )
            print(f"        Source: {it['source_name']} ({it['source_type']})  {it['date']}")
            print(f"        Title:  {it['title'][:72]}")
        if len(stats["items"]) > 30:
            print(f"\n  ... {len(stats['items']) - 30} more items not shown.")

    print(f"\n{'=' * 68}")
    if not dry_run and stats["auto_published"] > 0:
        print(
            f"Live run complete — {stats['auto_published']} incident(s) auto-published, "
            f"{stats['queued_for_review']} queued for review."
        )
    elif dry_run:
        print(
            "Dry run complete — review the tier breakdown above, "
            "then run without --dry-run to go live."
        )
    print(f"{'=' * 68}\n")

    sys.exit(0 if stats["errors"] == 0 else 1)
