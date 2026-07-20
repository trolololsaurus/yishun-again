"""Ingestion source adapters (INGESTION_DESIGN.md §3.2, §3.3)."""

from scrapers import scrape_mothership, scrape_reddit, scrape_straitstimes

from ingestion.sources.google_news_rss import GoogleNewsRSSSource
from ingestion.sources.legacy import LegacyScraperSource
from ingestion.sources.msm.cna import CNASource


def get_enabled_sources() -> list:
    """
    Live source list for run_ingestion_pass() (§10b step 10).

    PRIMARY (SG MSM, Q1=1b): CNA, Mothership, Straits Times — the main spine.
    CORROBORATION:           Google News RSS — cross-checks and catches misses.
    SOCIAL:                  Reddit (r/singapore, r/singaporeraw).

    Phase 1 of the adapter port (issue #23). The remaining legacy scrapers
    (AsiaOne, Stomp, MustShareNews, The Independent, Yahoo, Zaobao, Shin Min,
    Berita Harian, Tamil Murasu) are deliberately NOT registered yet: they emit
    no `published_at`, so every candidate would be dateless — bypassing the
    recency watermark, re-processed by Stage 1/2 on every pass, and blocked from
    approval until an operator types a date by hand (QA H3). They need date
    extraction first. EDMW (signal) additionally needs guardrail #2 handling
    (its URL must never reach source_urls) before it can be enabled.

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
            GoogleNewsRSSSource(),
            LegacyScraperSource(
                "reddit", scrape_reddit.scrape, source_type="reddit",
            ),
        ) if s.enabled
    ]
