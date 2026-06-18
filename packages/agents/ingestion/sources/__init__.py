"""Ingestion source adapters (INGESTION_DESIGN.md §3.2, §3.3)."""

from ingestion.sources.google_news_rss import GoogleNewsRSSSource
from ingestion.sources.msm.cna import CNASource


def get_enabled_sources() -> list:
    """
    Live source list for run_ingestion_pass() (§10b step 10).

    CNA (PRIMARY, msm) + Google News RSS (CORROBORATION) — the only two
    Source adapters built so far. Add new adapters here as they're built;
    main.py's pipeline job/endpoint don't need to change.
    """
    return [s for s in (CNASource(), GoogleNewsRSSSource()) if s.enabled]
