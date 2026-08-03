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

from ingestion.sources.legacy import LegacyScraperSource
from ingestion.sources.msm.cna import CNASource
from ingestion.sources.news_sitemap import news_sitemap_sources
from ingestion.sources.wp_search import wp_search_sources


def get_enabled_sources() -> list:
    """
    Live source list for run_ingestion_pass() (§10b step 10).

    PRIMARY (SG MSM, Q1=1b) — the main spine, reading each outlet's current
    feed or listing page:
        RSS-dated:  CNA, Mothership, Straits Times, MustShareNews,
                    The Independent, Yahoo
        HTML-dated: AsiaOne, Stomp, Zaobao, Shin Min, Berita Harian,
                    Tamil Murasu (date resolved per-article by
                    scrapers.resolve_published_at)

    DISCOVERY — the wider net behind the spine, added 2026-08-02 when Google
    News RSS was removed:
        news sitemaps:  the publisher's own Google-News sitemap (9 outlets).
                        Canonical URLs, real publication dates, and a far
                        bigger window than the front-page feed — 462 entries
                        for Straits Times against 44 in its RSS.
        WP search:      MustShareNews and The Independent answer
                        `?s=yishun&feed=rss2` with a dated RSS feed of search
                        results over their whole archive.

    Both discovery adapters emit the PUBLISHER's own URL. That is the point:
    google_news_rss emitted `news.google.com/rss/articles/<blob>` wrappers that
    frequently failed to resolve, breaking dedupe and putting a redirect where
    a citation belongs. See ingestion/sources/news_sitemap.py for the full
    account, and classifiers/source_allowlist.REDIRECT_DOMAINS for the net that
    now sits under it. Do not add an aggregator here.

    Registration is gated on a source supplying `published_at`: a dateless
    candidate bypasses the recency watermark, is re-processed by Stage 1/2 on
    every pass, and cannot be approved until an operator types a date by hand
    (QA H3). The RSS sources read it from the feed; the HTML sources resolve it
    from the article (URL path, else meta tags).

    SIGNAL:         Reddit (r/singapore, r/singaporeraw) and EDMW/HWZ —
                    corroboration count only, never a quoted source (guardrail
                    #2), never the event date. MSM is the sole authority for the
                    citation and the date. EDMW's date comes from the thread's
                    start time in the LISTING markup; the thread page is never
                    fetched and post content is never read.

    All 14 scrapers are registered (Phase 3 complete), plus 11 discovery
    adapters. Every one of the 12 MSM scrapers below is untouched by the
    2026-08-02 change — the discovery adapters were added ALONGSIDE them, not
    in place of them. The only source removed was GoogleNewsRSSSource.

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
            # DISCOVERY — publishers' own news sitemaps and WordPress search
            # feeds. These took over from GoogleNewsRSSSource on 2026-08-02;
            # they emit canonical publisher URLs, so their output dedupes
            # cleanly against the primary scrapers above rather than
            # manufacturing "update" proposals for stories already held.
            *news_sitemap_sources(),
            *wp_search_sources(),
            # SIGNAL sources — never a quoted source. Their URL must never reach
            # source_urls (guardrail #2); they contribute the forum-signal count
            # only, and a signal-only item stays in the queue until an operator
            # attaches an MSM source. Enforced in three independent places: the
            # orchestrator (is_signal_source), the allowlist (domain type
            # 'signal' is stripped), and Stage 2's multi-source formatter (signal
            # articles are never rendered into the prompt).
            #
            # Reddit joined this tier in July 2026 (was 'reddit'): it is
            # user-generated discussion, not verifiable journalism, and its post
            # date is not an event date — treating it as a source manufactured
            # duplicate cards for old events at recent post dates. MSM is the sole
            # authority for the citation and the event date. scrape_reddit emits
            # 'signal'; source_name still carries the subreddit for the operator.
            LegacyScraperSource(
                "reddit", scrape_reddit.scrape, source_type="signal",
            ),
            LegacyScraperSource(
                "edmw", scrape_edmw.scrape,
                source_name="HWZ EDMW", source_type="signal",
            ),
        ) if s.enabled
    ]
