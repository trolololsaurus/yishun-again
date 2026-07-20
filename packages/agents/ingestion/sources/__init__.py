"""Ingestion source adapters (INGESTION_DESIGN.md §3.2, §3.3)."""

from scrapers import (
    scrape_asiaone,
    scrape_beritaharian,
    scrape_edmw,
    scrape_mothership,
    scrape_mustsharenews,
    scrape_reddit,
    scrape_shinmin,
    scrape_stomp,
    scrape_straitstimes,
    scrape_tamilmurasu,
    scrape_theindependent,
    scrape_yahoo,
    scrape_zaobao,
)

from ingestion.sources.google_news_rss import GoogleNewsRSSSource
from ingestion.sources.legacy import LegacyScraperSource
from ingestion.sources.msm.cna import CNASource


def get_enabled_sources() -> list:
    """
    Live source list for run_ingestion_pass() (§10b step 10).

    PRIMARY (SG MSM, Q1=1b) — the main spine:
        RSS-dated:  CNA, Mothership, Straits Times, MustShareNews,
                    The Independent, Yahoo
        HTML-dated: AsiaOne, Stomp, Zaobao, Shin Min, Berita Harian,
                    Tamil Murasu (date resolved per-article by
                    scrapers.resolve_published_at)
    CORROBORATION:  Google News RSS — cross-checks and catches misses.
    SOCIAL:         Reddit (r/singapore, r/singaporeraw).

    Phases 1-2 of the adapter port (issue #23) — all 13 non-signal scrapers from
    the spec's "Live pipeline" inventory are now wired in.

    Registration is gated on a source supplying `published_at`: a dateless
    candidate bypasses the recency watermark, is re-processed by Stage 1/2 on
    every pass, and cannot be approved until an operator types a date by hand
    (QA H3). The RSS sources read it from the feed; the HTML sources resolve it
    from the article (URL path, else meta tags).

    SIGNAL:         EDMW/HWZ — corroboration count only, never a quoted source
                    (guardrail #2). Its date comes from the thread's start time in
                    the LISTING markup; the thread page is never fetched and post
                    content is never read.

    All 14 scrapers are now registered (Phase 3 complete).

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
            LegacyScraperSource(
                "asiaone", scrape_asiaone.scrape,
                source_name="AsiaOne", source_type="msm",
            ),
            LegacyScraperSource(
                "stomp", scrape_stomp.scrape,
                source_name="Stomp", source_type="msm",
            ),
            LegacyScraperSource(
                "zaobao", scrape_zaobao.scrape,
                source_name="Lianhe Zaobao", source_type="msm",
            ),
            LegacyScraperSource(
                "shinmin", scrape_shinmin.scrape,
                source_name="Shin Min Daily News", source_type="msm",
            ),
            LegacyScraperSource(
                "berita_harian", scrape_beritaharian.scrape,
                source_name="Berita Harian", source_type="msm",
            ),
            LegacyScraperSource(
                "tamil_murasu", scrape_tamilmurasu.scrape,
                source_name="Tamil Murasu", source_type="msm",
            ),
            GoogleNewsRSSSource(),
            LegacyScraperSource(
                "reddit", scrape_reddit.scrape, source_type="reddit",
            ),
            # SIGNAL — never a quoted source. Its URL must never reach
            # source_urls (guardrail #2); it contributes edmw_signal_count only,
            # and an EDMW-only item stays in the queue until an operator attaches
            # an MSM source. Enforced in three independent places: the
            # orchestrator (is_signal_source), the allowlist (domain type
            # 'signal' is stripped), and Stage 2's multi-source formatter (signal
            # articles are never rendered into the prompt).
            LegacyScraperSource(
                "edmw", scrape_edmw.scrape,
                source_name="HWZ EDMW", source_type="signal",
            ),
        ) if s.enabled
    ]
