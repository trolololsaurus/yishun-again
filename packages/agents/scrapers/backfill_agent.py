"""
Historical backfill agent — spec §14c.

Scrapes Google News (2015–2026) and Wikipedia (1990–2014) for historical
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

MAX_ITEMS_PER_RUN = 500    # hard cap — operator re-runs to get more

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Wikipedia scope: 1990–2014 (Google News covers 2015–2026)
WIKI_YEAR_MIN = 1990
WIKI_YEAR_MAX = 2014


# ── Cheap local pre-filter (runs before Groq — zero API cost) ────────────────
#
# Title-level keyword patterns that are almost always noise for this archive.
# Any item whose title contains one of these *and* contains NO override keyword
# is silently dropped before Stage 1, saving Groq quota.
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
    before spending a Groq Stage 1 call on it.

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


def _gnews_pub_date(entry) -> Optional[str]:
    """Return ISO date string from feedparser's published_parsed, or None."""
    pp = entry.get("published_parsed")
    if pp:
        try:
            return f"{pp.tm_year:04d}-{pp.tm_mon:02d}-{pp.tm_mday:02d}"
        except Exception:
            pass
    return None


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
            headers={"User-Agent": _BROWSER_UA},
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
            headers={"User-Agent": _BROWSER_UA},
        )
        resp.raise_for_status()
        return resp.json().get("query", {}).get("search", [])
    except Exception as exc:
        logger.warning("Wikipedia search error: %s", exc)
    return []


def _extract_wiki_candidates(text: str, article_title: str, seen_urls: set) -> list:
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
            "title":       title,
            "content":     para[:3000],
            "url":         wiki_url,
            "source_name": "Wikipedia",
            "source_type": "reference",
            "date":        f"{year}-01-01",
        })

    return candidates


def _scrape_wikipedia(seen_urls: set) -> list:
    """
    Fetch Wikipedia content for historical Yishun incidents (1990–2014).
    Returns list of candidate dicts.
    """
    from scrapers import content_matches_keywords

    all_candidates = []
    processed_article_urls: set = set()

    # Step 1: spec-mandated known articles
    for article_title in WIKI_ARTICLES:
        wiki_url = f"https://en.wikipedia.org/wiki/{article_title.replace(' ', '_')}"
        if wiki_url in processed_article_urls:
            continue
        processed_article_urls.add(wiki_url)

        text = _wiki_article_text(article_title)
        if not text:
            continue

        candidates = _extract_wiki_candidates(text, article_title, seen_urls)
        logger.debug("Wikipedia [%s]: %d Yishun sections", article_title, len(candidates))
        all_candidates.extend(candidates)
        time.sleep(GNEWS_RATE_LIMIT)

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

        candidates = _extract_wiki_candidates(text, title, seen_urls)
        all_candidates.extend(candidates)
        time.sleep(GNEWS_RATE_LIMIT)

    logger.info("Wikipedia: %d candidate sections collected", len(all_candidates))
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
        if not (1990 <= y <= 2026):
            return None
        return f"{y:04d}-{mo:02d}-{d:02d}T00:00:00+00:00"
    except ValueError:
        return None


def _build_incident_row(draft: dict, item: dict) -> dict:
    """Build an incidents table row for direct auto-publish."""
    raw_date    = item.get("date", "")
    parsed_iso  = _parse_source_date_to_iso(raw_date)

    if parsed_iso is None:
        published_at  = datetime.now(timezone.utc).isoformat()
        incident_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        logger.warning(
            "Backfill: source_date missing/unparseable %r for '%s' — published_at = NOW()",
            raw_date, draft.get("title", "")[:60],
        )
    else:
        published_at  = parsed_iso
        incident_date = parsed_iso[:10]   # "YYYY-MM-DD"

    hype = 1 if item.get("source_type") == "reference" else draft.get("hype_meter", 0)

    # Geocode if lat/lon still null after Stage 2
    lat = draft.get("latitude")
    lon = draft.get("longitude")
    if (lat is None or lon is None) and (draft.get("block_number") or draft.get("area_name")):
        try:
            from classifiers.geocoding import geocode_incident
            coords = geocode_incident(draft.get("block_number"), draft.get("area_name"))
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
        "source_urls":         [item["url"]],
        "hype_meter":          hype,
        "corroboration_count": 1,
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
        "source_timeline":     [],
        "latest_source_role":  "initial",
        "is_milestone":        False,
    }


def _build_queue_row(item: dict, draft: dict, consolidation=None, is_update: bool = False) -> dict:
    """Build a war_room_queue row for items going to operator review."""
    status = "update" if is_update else "pending"
    update_target_id = None
    agent_role = "initial"

    if consolidation is not None:
        update_target_id = consolidation.matched_incident_id
        agent_role = consolidation.agent_role_proposed

    date_missing = not _parse_source_date_to_iso(item.get("date", ""))
    row = {
        "raw_content": {
            **item,
            **draft,
            "_backfill":        True,
            "_backfill_source": item.get("source_type", "msm"),
            **({"_date_fallback": True} if date_missing else {}),
        },
        "source_url":              item["url"],
        "source_type":             item.get("source_type", "msm"),
        "proposed_title":          draft["title"],
        "proposed_summary":        draft["summary"],
        "proposed_classification": draft["classification"],
        "proposed_severity":       draft["severity"],
        "proposed_pixel_prompt":   draft.get("pixel_art_prompt", ""),
        "proposed_slug":           draft.get("slug", ""),
        "agent_confidence":        draft["confidence"],
        "corroboration_count":     1,
        "edmw_signal_count":       0,
        "status":                  status,
    }
    row["raw_content"]["agent_role_proposed"] = agent_role

    if update_target_id:
        row["update_target_incident_id"] = update_target_id

    return row


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
    link_validation_flags = link_validation_flags or {}
    link_validation       = link_validation or {}

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
            row = _build_queue_row(item, draft, consolidation, is_update=True)
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
        if dry_run:
            stats["auto_published"] += 1
            stats["items"].append(_dry_run_entry(item, draft, "AUTO-PUBLISH"))
            return

        incident_row = _build_incident_row(draft, item)
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
            row = _build_queue_row(item, draft, consolidation)
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


def _dedup_batch(processed: list, stats: dict) -> list:
    """
    Intra-batch duplicate detection: group by location+date, then judge per group.

    Algorithm:
      1. Group entries by (date <=7 days) AND (same block_number / area_name).
         Title-keyword overlap >= 3 used as fallback when Stage 2 found no location.
      2. For each group of 2+: ONE Haiku call presenting all articles at once.
         Returns {is_same, primary_index, role_assignments}.
      3. If is_same=True: merge into one card with role-tagged source_timeline.

    Haiku API calls = number of multi-item groups, not O(n^2) pairwise.
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

    # Step 3: judge and merge each multi-item group
    for group_indices in multi_groups:
        group_entries = [processed[i] for i in group_indices]

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


# ── Main runner ──────────────────────────────────────────────────────────────

def run_backfill(
    start_year: int = 2015,
    end_year: int = 2026,
    include_wikipedia: bool = True,
    dry_run: bool = False,
    limit: int = MAX_ITEMS_PER_RUN,
) -> dict:
    """
    Run the historical backfill.

    Args:
        start_year:        First year for Google News scrape (inclusive).
        end_year:          Last year for Google News scrape (inclusive, max 2026).
        include_wikipedia: Also scrape Wikipedia for 1990–2014 incidents.
        dry_run:           Run Stage 1/2/consolidation but write nothing to Supabase.

    Returns:
        Stats dict with counts for all outcomes.
    """
    from filters.stage1_filter import filter_content
    from filters.stage2_writer import write_stage2
    from classifiers.corroboration import check_duplicate, get_supabase_client
    from classifiers.consolidation import check as consolidation_check

    end_year  = min(end_year, 2026)
    start_year = max(start_year, 2015)

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
        "prefiltered":        0,    # cheap local rejects before Groq call
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

    if include_wikipedia and not stats["capped"]:
        logger.info("Backfill — collecting Wikipedia candidates (1990–2014)")
        wiki_items = _scrape_wikipedia(seen_urls)
        all_candidates.extend(wiki_items)
        stats["scraped"] += len(wiki_items)

    logger.info(
        "Backfill — %d raw candidates collected (capped=%s)",
        len(all_candidates), stats["capped"],
    )

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
        try:
            s1 = filter_content(item)
        except Exception as exc:
            stats["errors"] += 1
            if len(stats["error_details"]) < 100:
                stats["error_details"].append({
                    "phase": "stage1", "url": url[:120],
                    "error": f"{type(exc).__name__}: {exc}",
                })
            logger.error("Stage 1 error [%s]: %s", url[:80], exc)
            continue

        if not s1["passes"]:
            stats["stage1_rejected"] += 1
            continue

        stats["stage1_passed"] += 1

        # ── Stage 2 ──────────────────────────────────────────────────────────
        try:
            stage2_input = {
                **item,
                "source_urls":       [] if is_wiki else [url],
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

    dry_run = "--dry-run" in sys.argv
    no_wiki = "--no-wikipedia" in sys.argv

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

    print(f"\n{'=' * 68}")
    print(f"Yishun Again — Historical Backfill {'DRY RUN' if dry_run else 'LIVE RUN'}")
    print(f"  Google News:  {start_year}–{end_year}")
    print(f"  Wikipedia:    {'yes (1990–2014)' if not no_wiki else 'no'}")
    print(f"  Limit:        {limit} items")
    print(f"  Auto-publish: confidence >= {TIER_AUTO_PUBLISH}")
    print(f"  Queue:        confidence {TIER_QUEUE}–{TIER_AUTO_PUBLISH - 0.01:.2f}")
    print(f"  Reject:       confidence < {TIER_QUEUE}")
    if dry_run:
        print("  NOTE: DRY RUN — no writes to Supabase")
    print(f"{'=' * 68}\n")

    stats = run_backfill(
        start_year         = start_year,
        end_year           = end_year,
        include_wikipedia  = not no_wiki,
        dry_run            = dry_run,
        limit              = limit,
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
        print(f"\n{'─' * 68}")
        print(f"ERROR BREAKDOWN ({len(stats['error_details'])} captured, cap=100):")
        print(f"{'─' * 68}")
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
        print(f"\n{'─' * 68}")
        print(f"ITEMS SAMPLED ({len(stats['items'])} shown):")
        print(f"{'─' * 68}")
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
