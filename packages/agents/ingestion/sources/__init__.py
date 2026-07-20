"""Ingestion source adapters (INGESTION_DESIGN.md §3.2, §3.3)."""

from scrapers import (
    scrape_mothership,
    scrape_mustsharenews,
    scrape_reddit,
    scrape_straitstimes,
    scrape_theindependent,
    scrape_yahoo,
)

from ingestion.sources.google_news_rss import GoogleNewsRSSSource
from ingestion.sources.legacy import LegacyScraperSource
from ingestion.sources.msm.cna import CNASource


def get_enabled_sources() -> list:
    """
    Live source list for run_ingestion_pass() (§10b step 10).

    PRIMARY (SG MSM, Q1=1b): CNA, Mothership, Straits Times, MustShareNews,
                             The Independent, Yahoo — the main spine.
    CORROBORATION:           Google News RSS — cross-checks and catches misses.
    SOCIAL:                  Reddit (r/singapore, r/singaporeraw).

    Phases 1-2a of the adapter port (issue #23). Every source here is RSS-backed
    and supplies `published_at`, which is the gate for registration: a dateless
    candidate bypasses the recency watermark, is re-processed by Stage 1/2 on
    every pass, and cannot be approved until an operator types a date by hand
    (QA H3).

    Still unregistered — the six HTML-scraped sources (AsiaOne, Stomp, Zaobao,
    Shin Min, Berita Harian, Tamil Murasu). They scrape listing pages that carry
    no date, so they need per-article date extraction first (Phase 2b). EDMW
    (signal) additionally needs guardrail #2 handling — its URL must never reach
    source_urls — before it can be enabled (Phase 3).

    Add new adapters here as they're built; main.py's pipeline job/endpoint
    don't need to change.
    """
    return [
        s for s in (
            CNASource(),
            LegacyScraperSource(
                "mothership", scrape_mothership.scrape,
                source_name="Mothership", source_type="msm",
            ),
            LegacyScraperSource(
                "straits_times", scrape_straitstimes.scrape,
                source_name="The Straits Times", source_type="msm",
            ),
            LegacyScraperSource(
                "mustsharenews", scrape_mustsharenews.scrape,
                source_name="MustShareNews", source_type="msm",
            ),
            LegacyScraperSource(
                "the_independent", scrape_theindependent.scrape,
                source_name="The Independent Singapore", source_type="msm",
            ),
            LegacyScraperSource(
                "yahoo", scrape_yahoo.scrape,
                source_name="Yahoo News Singapore", source_type="msm",
            ),
            GoogleNewsRSSSource(),
            LegacyScraperSource(
                "reddit", scrape_reddit.scrape, source_type="reddit",
            ),
        ) if s.enabled
    ]
