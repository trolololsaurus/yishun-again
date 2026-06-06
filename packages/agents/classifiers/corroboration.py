"""
Corroboration agent (spec §4.4).

Responsibilities:
  - compute_hype_meter: count unique MSM sources in source_urls (capped at 5)
  - check_duplicate:    return True if URL already exists in queue or incidents
  - get_supabase_client: shared admin client factory for agent writes

Full cross-source incident matching (date + location + type similarity) is a
future enhancement. For now corroboration_count is 1 at queue time and is
updated by the operator or a later enrichment pass.
"""

import logging
import os

from dotenv import load_dotenv

load_dotenv(override=False)

logger = logging.getLogger(__name__)

# MSM domains that count toward hype_meter — from spec §7.
MSM_DOMAINS = [
    "channelnewsasia", "straitstimes", "mothership", "stomp",
    "mustsharenews", "theindependent", "zaobao", "shinmin",
    "beritaharian", "tamilmurasu", "yahoo", "asiaone", "jom",
]


def get_supabase_client():
    """
    Return a Supabase admin client (bypasses RLS).
    Import is lazy so the module can be imported without the package installed.
    """
    from supabase import create_client  # type: ignore[import]

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SECRET_KEY")
    if not url or not key:
        raise EnvironmentError(
            "SUPABASE_URL and SUPABASE_SECRET_KEY must be set. "
            "Add them to .env or Cloud Run environment variables."
        )
    return create_client(url, key)


def compute_hype_meter(source_urls: list[str]) -> int:
    """
    Count unique MSM source domains in source_urls, capped at 5.
    EDMW/Reddit URLs are never in source_urls so they never inflate the meter.
    """
    seen_domains: set[str] = set()
    for url in source_urls:
        url_lower = url.lower()
        for domain in MSM_DOMAINS:
            if domain in url_lower and domain not in seen_domains:
                seen_domains.add(domain)
                break  # one match per URL is enough
    return min(5, len(seen_domains))


def check_duplicate(url: str, client=None) -> bool:
    """
    Return True if this URL is already in war_room_queue or incidents.

    Args:
        url:    The primary source URL to check.
        client: Optional pre-built Supabase client. A new one is created if None.
                Pass None and let this function handle connection errors gracefully.

    Returns:
        False on any connection error (conservative — allow re-processing rather
        than silently dropping new content).
    """
    if not url:
        return False

    try:
        if client is None:
            client = get_supabase_client()

        # Check war_room_queue (source_url column)
        result = (
            client.table("war_room_queue")
            .select("id")
            .eq("source_url", url)
            .limit(1)
            .execute()
        )
        if result.data:
            logger.debug("Duplicate found in queue: %s", url)
            return True

        # Check incidents (source_urls is TEXT[] — use contains)
        result = (
            client.table("incidents")
            .select("id")
            .contains("source_urls", [url])
            .limit(1)
            .execute()
        )
        if result.data:
            logger.debug("Duplicate found in incidents: %s", url)
            return True

        return False

    except EnvironmentError:
        logger.warning("Supabase not configured — skipping duplicate check for %s", url)
        return False
    except Exception as exc:
        logger.warning("Duplicate check failed for %s: %s — treating as new", url, exc)
        return False
